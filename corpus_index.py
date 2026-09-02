"""
Index and mine the entire shipped BG3 stats corpus.

Parses every `new entry` in the extracted game data into a queryable JSON index,
then mines it for the *mechanism vocabulary* — every functor, condition helper,
entity keyword and field actually used by Larian, with real frequencies and
citations back to a concrete vanilla example.

The point is not to summarise 9,500 entries in prose. It is to answer, instantly
and from ground truth, the questions that actually come up while modding:
  - does functor X exist, and what does a real call to it look like?
  - what fields does a SpellData/PassiveData/InterruptData/StatusData carry?
  - what are the legal values of enum Y?
  - which vanilla entries do the thing I'm trying to do?

The LSX half of the corpus (progressions, spell lists, root templates, tags,
VFX banks) is indexed alongside the stats, so a GUID can be resolved to the node
that defines it and to everything that points at it.

Usage:
    py corpus_index.py                          # build both indexes + report
    py corpus_index.py --skip-lsx               # stats only (fast)
    py corpus_index.py --find SwapPlaces        # every call site of a functor
    py corpus_index.py --entry Interrupt_Parry  # one stats entry, `using` resolved
    py corpus_index.py --uuid <guid>            # what defines it / what points at it
    py corpus_index.py --lsx-name Fighter       # GUID of a named LSX node
    py corpus_index.py --lsx-node Progression   # which attributes a node type carries
    py corpus_index.py --lsx-grep Warpblade     # raw search across the LSX corpus
    py corpus_index.py --verify-lsx             # tokeniser vs ElementTree self-check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Paths come from the mod's forge.json rather than constants here. One codebase has to
# serve every mod built with this toolchain, and a COPIED tool is a frozen tool - it stops
# receiving fixes the moment it is duplicated. forge/ is the framework layer; this project
# is a consumer of it.
_FORGE = Path(__file__).resolve().parent          # this file now LIVES in forge/
if str(_FORGE) not in sys.path:
    sys.path.insert(0, str(_FORGE))
import modconfig  # noqa: E402

try:
    CFG = modconfig.load(Path.cwd())
except modconfig.ConfigError as _e:
    # Every tool in this directory imports this module, so catching here gives all of
    # them the same clean refusal instead of a traceback. The message already says what
    # to do; a stack trace on top of it only buries the one line that matters.
    print(f"cannot locate this mod: {_e}", file=sys.stderr)
    raise SystemExit(2)
UNPACKED = CFG.unpacked
OUT_DIR = CFG.corpus

# Roots that contain shipped Stats/Generated/Data trees.
STATS_ROOTS = [
    UNPACKED / "Shared/Public/Shared/Stats/Generated",
    UNPACKED / "Shared/Public/SharedDev/Stats/Generated",
    UNPACKED / "GustavX/Public/GustavX/Stats/Generated",
    UNPACKED / "GustavX/Public/HonourX/Stats/Generated",
    # Gustav.pak, added 2026-08-19. It had never been extracted, and its absence
    # cost real time twice in one session: the whole Booming Blade base family
    # (Target_BoomingBlade, BoomingBlade_Movement_Passive,
    # BOOMING_BLADE_DAMAGE_IMMUNITY) was referenced by GustavX but missing, and so
    # was the entire MAG_* magic-item status library that Reverberation lives in -
    # which made a correct, vanilla-shaped call look unattested to the validator.
    # Extract with:
    #   Divine.exe -g bg3 -a extract-package -s "<game>/Data/Gustav.pak"
    #              -d C:\Modding\bg3_unpacked\Gustav -x "*/Stats/Generated/Data/*.txt"
    # (this line lost a backslash to a shell heredoc once already, leaving a literal
    # 0x08 BACKSPACE in the source where the b of bg3 should have been - rule 4.13)
    UNPACKED / "Gustav/Public/Gustav/Stats/Generated",
    UNPACKED / "Gustav/Public/GustavDev/Stats/Generated",
    UNPACKED / "Gustav/Public/Honour/Stats/Generated",
]

# Fields whose values are functor lists (things that DO something).
FUNCTOR_FIELDS = {
    "SpellProperties", "SpellSuccess", "SpellFail", "Properties", "StatsFunctors",
    "OnApplyFunctors", "OnRemoveFunctors", "OnTickFunctors", "OnDamageFunctors",
    "TargetEffect", "Boosts", "PassivesOnEquip", "SpellRoll", "AuraStatuses",
    "OnHitFunctors", "OnSaveFunctors", "OnSuccess", "OnFailure",
    # Tooltip/description fields carry real functor expressions too (Distance(),
    # DealDamage(), LevelMapValue()); they must be mined or the vocabulary has
    # holes that read as "invented functor" to validate.py.
    "DescriptionParams", "ExtraDescriptionParams", "ShortDescriptionParams",
    "TooltipDamageList", "TooltipStatusApply", "TooltipOnSave", "TooltipUseCosts",
    "TooltipDamage", "TooltipUpcastDescriptionParams",
    # Status lifecycle + equipment + toggle functors. Missing these left holes
    # that read as "invented functor" to validate.py (see its field-context check).
    "DefaultBoosts", "TickFunctors", "BoostsOnEquipMainHand", "BoostsOnEquipOffHand",
    "OnTickSuccess", "OnTickFail", "OnApplySuccess", "OnApplyFail",
    "ToggleOnFunctors", "ToggleOffFunctors", "Success", "Failure",
}
# Fields whose values are boolean condition expressions.
CONDITION_FIELDS = {
    "Conditions", "TargetConditions", "RequirementConditions", "EnableCondition",
    "SpellRoll", "Requirements", "CanBeUsedInCombat", "AreaRadius",
    "RemoveConditions", "CycleConditions", "BoostConditions", "UseConditions",
    "EnabledConditions", "ExtraProjectileTargetConditions", "AoEConditions",
    "OnTickRoll", "OnApplyRoll", "Roll",
}

ENTITY_TOKENS = {
    "SELF", "SWAP", "OBSERVER_OBSERVER", "OBSERVER_SOURCE", "OBSERVER_TARGET",
    "SOURCE", "TARGET", "CAUSE", "OWNER",
}

ENTRY_RE = re.compile(r'^new entry "([^"]*)"')
TYPE_RE = re.compile(r'^type "([^"]*)"')
USING_RE = re.compile(r'^using "([^"]*)"')
DATA_RE = re.compile(r'^data "([^"]*)" "(.*)"\s*$')

# An identifier immediately followed by '(' — a functor or condition call.
CALL_RE = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')
# A bare UPPERCASE token followed by ':' used as a targeting prefix.
PREFIX_RE = re.compile(r'(?:^|;)\s*([A-Z][A-Z0-9_]*)\s*:')


def parse_file(path: Path) -> list[dict]:
    """Parse one stats .txt into a list of entry dicts."""
    entries: list[dict] = []
    cur: dict | None = None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue

        m = ENTRY_RE.match(line)
        if m:
            cur = {
                "name": m.group(1),
                "type": None,
                "using": None,
                "file": path.name,
                "data": {},
            }
            entries.append(cur)
            continue

        if cur is None:
            continue

        m = TYPE_RE.match(line)
        if m:
            cur["type"] = m.group(1)
            continue

        m = USING_RE.match(line)
        if m:
            cur["using"] = m.group(1)
            continue

        m = DATA_RE.match(line)
        if m:
            # Later duplicate keys win, matching engine behaviour.
            cur["data"][m.group(1)] = m.group(2)

    return entries


def build_index() -> list[dict]:
    entries: list[dict] = []
    seen_files = 0
    for root in STATS_ROOTS:
        data_dir = root / "Data"
        if not data_dir.is_dir():
            continue
        for path in sorted(data_dir.glob("*.txt")):
            found = parse_file(path)
            if found:
                seen_files += 1
                for e in found:
                    # .../<Root>/Stats/Generated -> Shared / SharedDev / GustavX / HonourX
                    e["root"] = root.parts[-3]
                entries.extend(found)
    print(f"parsed {seen_files} files -> {len(entries)} entries", file=sys.stderr)
    return entries


def resolve_using(entries: list[dict]) -> dict[str, dict]:
    """Map name -> entry, and record the full inheritance chain for each."""
    by_name = {e["name"]: e for e in entries}
    for e in entries:
        chain, cursor, guard = [], e.get("using"), 0
        while cursor and guard < 32:
            chain.append(cursor)
            parent = by_name.get(cursor)
            cursor = parent.get("using") if parent else None
            guard += 1
        e["using_chain"] = chain
    return by_name


def effective_fields(entry: dict, by_name: dict[str, dict]) -> dict[str, str]:
    """Fields an entry actually has, following `using` inheritance."""
    merged: dict[str, str] = {}
    for parent_name in reversed(entry.get("using_chain", [])):
        parent = by_name.get(parent_name)
        if parent:
            merged.update(parent["data"])
    merged.update(entry["data"])
    return merged


def mine(entries: list[dict]) -> dict:
    """Extract the mechanism vocabulary from every entry."""
    functors: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "fields": Counter(), "types": Counter(), "example": None}
    )
    conditions: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "fields": Counter(), "types": Counter(), "example": None}
    )
    prefixes: dict[str, dict] = defaultdict(lambda: {"count": 0, "example": None})
    # Functor(ENTITY, ...) leading-argument census.
    leading: dict[tuple[str, str], int] = Counter()
    fields_by_type: dict[str, Counter] = defaultdict(Counter)
    type_counts: Counter = Counter()

    for e in entries:
        etype = e["type"] or "?"
        type_counts[etype] += 1
        for key, val in e["data"].items():
            fields_by_type[etype][key] += 1
            if not val:
                continue

            bucket = None
            if key in FUNCTOR_FIELDS:
                bucket = functors
            elif key in CONDITION_FIELDS:
                bucket = conditions
            if bucket is None:
                continue

            for name in CALL_RE.findall(val):
                rec = bucket[name]
                rec["count"] += 1
                rec["fields"][key] += 1
                rec["types"][etype] += 1
                if rec["example"] is None:
                    rec["example"] = {"entry": e["name"], "field": key,
                                      "value": val[:400], "file": e["file"]}

            for tok in PREFIX_RE.findall(val):
                rec = prefixes[tok]
                rec["count"] += 1
                if rec["example"] is None:
                    rec["example"] = {"entry": e["name"], "field": key,
                                      "value": val[:240], "file": e["file"]}

            for fn, arg in re.findall(
                r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([A-Z][A-Z0-9_]*)\s*[,)]', val
            ):
                if arg in ENTITY_TOKENS:
                    leading[(fn, arg)] += 1

    def flatten(bucket: dict) -> list[dict]:
        out = []
        for name, rec in bucket.items():
            out.append({
                "name": name,
                "count": rec["count"],
                "fields": dict(rec["fields"].most_common()),
                "types": dict(rec["types"].most_common()),
                "example": rec["example"],
            })
        return sorted(out, key=lambda r: -r["count"])

    return {
        "functors": flatten(functors),
        "conditions": flatten(conditions),
        "prefixes": sorted(
            ({"token": k, **v} for k, v in prefixes.items()),
            key=lambda r: -r["count"],
        ),
        "leading_entity_args": sorted(
            ({"functor": f, "entity": a, "count": c} for (f, a), c in leading.items()),
            key=lambda r: -r["count"],
        ),
        "fields_by_type": {t: dict(c.most_common()) for t, c in fields_by_type.items()},
        "type_counts": dict(type_counts.most_common()),
    }


def parse_valuelists() -> dict[str, list[str]]:
    """Extract every enum from ValueLists.txt — the legal values for enum fields."""
    out: dict[str, list[str]] = {}
    for root in STATS_ROOTS:
        path = root / "Structure/Base/ValueLists.txt"
        if not path.is_file():
            continue
        cur = None
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            m = re.match(r'^valuelist "([^"]+)"', line)
            if m:
                cur = m.group(1)
                out.setdefault(cur, [])
                continue
            m = re.match(r'^value "([^"]*)"', line)
            if m and cur:
                out[cur].append(m.group(1))
    return out


# ---------------------------------------------------------------------------
# LSX corpus — the other half of the shipped data
#
# The stats .txt files above are only part of what ships. Progressions, spell
# lists, class descriptions, root templates, tags, flags and the VFX banks all
# live in LSX/LSF. 8,228 .lsf + 483 .lsfx files ship as compiled binary and are
# not greppable at all, which left a hole in the middle of this corpus: every
# question about progressions, subclass wiring or spell visuals was unanswerable
# from ground truth.
#
# Fix is one batch conversion with the LSLib CLI we already drive from build.ps1:
#
#   Divine.exe -g bg3 -a convert-resources -s <unpacked> -d <unpacked_lsx> \
#              -i lsf -o lsx
#
# It recurses and preserves directory structure, and -i lsf picks up .lsfx too.
# Re-run it after a game patch. The two trees are indexed together: UNPACKED
# holds the files that already shipped as .lsx, UNPACKED_LSX the converted ones.
# ---------------------------------------------------------------------------

UNPACKED_LSX = CFG.unpacked_lsx

# Which GUID-valued attribute IS a node's own identity, as opposed to a pointer
# at something defined elsewhere. This is measured, not assumed: treating every
# UUID/MapKey/ID/GUID as an identity made 39.6% of GUIDs claim two different
# defining node types, because `<Object><ID>` in the material and effect banks is
# a parameter pointer, not an identity. The rule below is the one that survives
# the data — 0.73% collisions over 304k GUIDs, and the residue is real structural
# duplication (a flag declared under both `flag` and `Flags`), not a bad rule.
#
#   UUID                        identity everywhere (Tags, Progressions, Flags,
#                               EffectInfo, ClassDescriptions, Actor, ...)
#   GameObjects  + MapKey       root templates key themselves by MapKey
#   Resource     + ID           material / texture / effect resource banks
#   EffectComponent + ID        effect component definitions
#
# Re-derive with --identity-audit if a game patch changes the schema.
IDENTITY_ATTR = "UUID"
IDENTITY_PAIRS = {
    ("GameObjects", "MapKey"),
    ("Resource", "ID"),
    ("EffectComponent", "ID"),
}

# Attributes worth keeping as a human-readable label for an identity node.
LABEL_ATTRS = ("Name", "DisplayName", "TemplateName", "Label", "Description")

GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# LSX is machine-generated by LSLib, so a tokeniser is safe here and is far
# faster than ElementTree over 1.4 GB. `--verify-lsx` re-parses a random sample
# with ElementTree and asserts the node counts agree — run it after any change
# to this regex, and after an LSLib upgrade.
LSX_TOK = re.compile(
    r"<(?P<close>/?)(?P<tag>node|region|attribute)\b(?P<body>[^>]*?)(?P<self>/?)>")
LSX_ATTR = re.compile(r'([A-Za-z_][\w]*)="([^"]*)"')

# Reference sites stored per GUID. Enough to answer "who points at this?"
# without the index blowing up on GUIDs referenced thousands of times.
MAX_REF_SITES = 25


def iter_lsx_files() -> list[Path]:
    """Every .lsx in both trees: the shipped ones and the converted ones."""
    out: list[Path] = []
    for base in (UNPACKED, UNPACKED_LSX):
        if base.is_dir():
            out.extend(sorted(base.rglob("*.lsx")))
    return out


def parse_lsx(text: str):
    """Walk one LSX, yielding (region, node_id, attrs) for every node.

    attrs maps attribute id -> (type, value). TranslatedString attributes carry
    a `handle` instead of a `value`; the handle is stored as the value so a
    handle can be traced back to the node that uses it.
    """
    stack: list[tuple[str, dict]] = []
    region = ""
    for m in LSX_TOK.finditer(text):
        tag, closing, selfclose = m["tag"], m["close"], m["self"]
        body = m["body"]

        if tag == "region":
            if not closing:
                region = dict(LSX_ATTR.findall(body)).get("id", "")
            continue

        if tag == "attribute":
            if not stack:
                continue
            a = dict(LSX_ATTR.findall(body))
            aid = a.get("id")
            if aid:
                stack[-1][1][aid] = (a.get("type", ""),
                                     a.get("value", a.get("handle", "")))
            continue

        # tag == "node"
        if closing:
            if stack:
                nid, attrs = stack.pop()
                yield region, nid, attrs
            continue

        nid = dict(LSX_ATTR.findall(body)).get("id", "")
        if selfclose:
            yield region, nid, {}
        else:
            stack.append((nid, {}))

    # Tolerate a truncated file rather than losing everything parsed so far.
    while stack:
        nid, attrs = stack.pop()
        yield region, nid, attrs


def build_lsx_index() -> dict:
    """Index every LSX node that defines a GUID, plus every reference to one."""
    files = iter_lsx_files()
    if not files:
        print(f"no .lsx found under {UNPACKED} or {UNPACKED_LSX} - "
              f"run the Divine convert-resources pass (see header)", file=sys.stderr)
        return {}

    file_table: list[str] = []
    defs: dict[str, list] = {}          # guid -> [node_id, label, file_idx, region]
    refs: dict[str, list] = {}          # guid -> [[file_idx, attr_id, node_id], ...]
    ref_counts: Counter = Counter()
    schema: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "attrs": {}, "example": None})
    region_counts: Counter = Counter()
    dupes: list[tuple[str, str, str]] = []

    for fi, path in enumerate(files):
        # Store the path relative to whichever tree it came from, tagged with
        # the tree, so a reader can tell converted data from shipped data.
        try:
            rel = "src/" + path.relative_to(UNPACKED).as_posix()
        except ValueError:
            rel = "conv/" + path.relative_to(UNPACKED_LSX).as_posix()
        file_table.append(rel)

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for region, nid, attrs in parse_lsx(text):
            region_counts[region] += 1
            s = schema[nid]
            s["count"] += 1
            if s["example"] is None:
                s["example"] = fi

            own_guid = None
            for aid, (atype, val) in attrs.items():
                rec = s["attrs"].setdefault(aid, {"count": 0, "type": atype})
                rec["count"] += 1

                if not GUID_RE.match(val):
                    continue
                is_identity = (aid == IDENTITY_ATTR or (nid, aid) in IDENTITY_PAIRS)
                if is_identity and own_guid is None:
                    own_guid = val
                else:
                    ref_counts[val] += 1
                    sites = refs.setdefault(val, [])
                    if len(sites) < MAX_REF_SITES:
                        sites.append([fi, aid, nid])

            if own_guid:
                label = ""
                for k in LABEL_ATTRS:
                    if k in attrs and attrs[k][1]:
                        label = attrs[k][1][:120]
                        break
                if own_guid in defs and defs[own_guid][0] != nid:
                    dupes.append((own_guid, defs[own_guid][0], nid))
                defs.setdefault(own_guid, [nid, label, fi, region])

        if fi % 1500 == 0 and fi:
            print(f"  ...{fi}/{len(files)} files, {len(defs)} defined GUIDs",
                  file=sys.stderr)

    print(f"parsed {len(files)} lsx files -> {len(defs)} defined GUIDs, "
          f"{len(refs)} referenced GUIDs, {len(schema)} node types", file=sys.stderr)
    if dupes:
        print(f"  note: {len(dupes)} GUIDs defined by more than one node type "
              f"(first wins; e.g. {dupes[0]})", file=sys.stderr)

    return {
        "files": file_table,
        "defs": defs,
        "refs": refs,
        "ref_counts": dict(ref_counts.most_common()),
        "schema": {k: {"count": v["count"],
                       "example": v["example"],
                       "attrs": dict(sorted(v["attrs"].items(),
                                            key=lambda kv: -kv[1]["count"]))}
                   for k, v in sorted(schema.items(), key=lambda kv: -kv[1]["count"])},
        "regions": dict(region_counts.most_common()),
    }


def load_lsx() -> dict:
    path = OUT_DIR / "lsx_index.json"
    if not path.is_file():
        print(f"no {path} - run `py corpus_index.py` first to build it",
              file=sys.stderr)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_uuid(lsx: dict, guid: str) -> None:
    """Resolve a GUID: what defines it, and everything that points at it."""
    guid = guid.strip().strip('"')
    files = lsx["files"]
    d = lsx["defs"].get(guid)
    if d:
        nid, label, fi, region = d
        print(f'DEFINED  <node id="{nid}">' + (f'  Name="{label}"' if label else ""))
        print(f"         region={region}")
        print(f"         {files[fi]}")
    else:
        print("DEFINED  (nothing in the shipped corpus defines this GUID)")

    total = lsx["ref_counts"].get(guid, 0)
    sites = lsx["refs"].get(guid, [])
    print(f"\nREFERENCED {total} time(s)"
          + (f" (showing first {len(sites)})" if total > len(sites) else ""))
    for fi, aid, nid in sites:
        print(f'  {aid:<28} on <node id="{nid}">   {files[fi]}')
    if not sites:
        print("  (no references - a dangling or unused GUID)")


def cmd_lsx_node(lsx: dict, node_id: str) -> None:
    """Schema for one LSX node type: which attributes it really carries."""
    schema, files = lsx["schema"], lsx["files"]
    exact = schema.get(node_id)
    if not exact:
        near = [n for n in schema if node_id.lower() in n.lower()][:30]
        print(f"no node type {node_id!r}."
              + ("  did you mean:\n  " + "\n  ".join(near) if near else ""))
        return
    print(f'<node id="{node_id}">   {exact["count"]:,} instances')
    print(f'  example: {files[exact["example"]]}')
    print(f'  {len(exact["attrs"])} distinct attributes:')
    for aid, rec in exact["attrs"].items():
        pct = 100.0 * rec["count"] / exact["count"]
        print(f'    {aid:<34} {rec["type"]:<18} {rec["count"]:>8,}  ({pct:5.1f}%)')


def cmd_lsx_name(lsx: dict, needle: str) -> None:
    """Find identity nodes whose label matches — 'what is the UUID of X'."""
    files, hits = lsx["files"], 0
    low = needle.lower()
    for guid, (nid, label, fi, region) in lsx["defs"].items():
        if low in label.lower():
            hits += 1
            if hits <= 60:
                print(f'{guid}  <{nid}>  "{label}"   {files[fi]}')
    print(f"\n{hits} identity node(s) whose label matches {needle!r}"
          + ("  (showing 60)" if hits > 60 else ""), file=sys.stderr)


def cmd_lsx_grep(pattern: str, limit: int = 80) -> None:
    """Raw search across the whole LSX corpus, printing the matching lines."""
    rx = re.compile(pattern, re.I)
    hits = 0
    for path in iter_lsx_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not rx.search(text):
            continue
        for line in text.splitlines():
            if rx.search(line):
                hits += 1
                if hits <= limit:
                    print(f"{path.name}: {line.strip()[:300]}")
                    if hits == limit:
                        print(f"  ... (capped at {limit}; narrow the pattern)")
        if hits > limit * 40:
            break
    print(f"\n{hits} matching line(s) for {pattern!r}", file=sys.stderr)


def identity_audit(top: int = 25) -> int:
    """Re-derive the identity rule from the data, and report how it holds up.

    Prints the collision rate of the rule in force plus the biggest (node,
    attribute) pairs carrying GUIDs, so IDENTITY_PAIRS can be re-checked after a
    game patch rather than trusted forever.
    """
    from collections import Counter as _C
    defs: dict[str, str] = {}
    dupes: _C = _C()
    pairs: _C = _C()
    for path in iter_lsx_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for _region, nid, attrs in parse_lsx(text):
            own = None
            for aid, (_ty, val) in attrs.items():
                if not GUID_RE.match(val):
                    continue
                pairs[(nid, aid)] += 1
                if own is None and (aid == IDENTITY_ATTR
                                    or (nid, aid) in IDENTITY_PAIRS):
                    own = val
            if own:
                if own in defs and defs[own] != nid:
                    dupes[(defs[own], nid)] += 1
                defs.setdefault(own, nid)

    total = sum(dupes.values())
    pct = 100.0 * total / max(len(defs), 1)
    print(f"identity rule in force: UUID + {sorted(IDENTITY_PAIRS)}")
    print(f"  {len(defs):,} defined GUIDs, {total:,} collisions ({pct:.2f}%)")
    print(f"\n  worst collisions (a GUID claimed by two node types):")
    for (a, b), n in dupes.most_common(10):
        print(f"    {n:>6}  <{a}> vs <{b}>")
    print(f"\n  biggest GUID-carrying (node, attribute) pairs - any of these with a")
    print(f"  high count that is NOT in the rule may be an identity we are missing:")
    for (nid, aid), n in pairs.most_common(top):
        mark = "  <- identity" if (aid == IDENTITY_ATTR
                                   or (nid, aid) in IDENTITY_PAIRS) else ""
        print(f"    {n:>8}  <{nid}>.{aid}{mark}")
    return 0


def verify_lsx(sample: int = 40) -> int:
    """Cross-check the fast tokeniser against a real XML parser.

    LSX_TOK is a regex over machine-generated XML. That is a deliberate speed
    trade, so it needs a correctness check that does not depend on it: parse a
    random sample with ElementTree and assert the node counts agree.
    """
    import random
    import xml.etree.ElementTree as ET

    files = iter_lsx_files()
    if not files:
        print("no lsx files to verify", file=sys.stderr)
        return 1
    random.seed(1234)
    picked = random.sample(files, min(sample, len(files)))

    bad = 0
    for path in picked:
        text = path.read_text(encoding="utf-8", errors="replace")
        mine = sum(1 for _ in parse_lsx(text))
        try:
            theirs = sum(1 for _ in ET.fromstring(text).iter("node"))
        except ET.ParseError as exc:
            print(f"  SKIP (not well-formed XML): {path.name}: {exc}")
            continue
        if mine != theirs:
            bad += 1
            print(f"  MISMATCH {path}: tokeniser={mine} elementtree={theirs}")
    print(f"verified {len(picked)} files: {len(picked) - bad} agree, {bad} mismatch",
          file=sys.stderr)
    return 1 if bad else 0


def cmd_find(entries: list[dict], needle: str) -> None:
    """Print every real call site of a functor/condition name."""
    hits = 0
    for e in entries:
        for key, val in e["data"].items():
            if re.search(rf'\b{re.escape(needle)}\s*\(', val or ""):
                hits += 1
                print(f'{e["name"]}  [{e["type"]}, {e["file"]}]')
                print(f'    {key} = {val[:500]}')
    print(f"\n{hits} call site(s) for {needle!r}", file=sys.stderr)


def cmd_entry(entries: list[dict], by_name: dict[str, dict], name: str) -> None:
    e = by_name.get(name)
    if not e:
        near = [n for n in by_name if name.lower() in n.lower()][:25]
        print(f"no entry named {name!r}." + (f" did you mean:\n  " + "\n  ".join(near) if near else ""))
        return
    print(f'{e["name"]}   type={e["type"]}   file={e["file"]}   root={e["root"]}')
    if e["using_chain"]:
        print(f'  using chain: {" -> ".join(e["using_chain"])}')
    print("  --- own fields ---")
    for k, v in e["data"].items():
        print(f'    {k} = {v}')
    inherited = {k: v for k, v in effective_fields(e, by_name).items() if k not in e["data"]}
    if inherited:
        print("  --- inherited ---")
        for k, v in inherited.items():
            print(f'    {k} = {v}')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--find", metavar="FUNCTOR", help="print every call site of a functor")
    ap.add_argument("--entry", metavar="NAME", help="dump one entry, resolving `using`")
    ap.add_argument("--uuid", metavar="GUID", help="resolve a GUID: definition + references")
    ap.add_argument("--lsx-name", metavar="TEXT", help="find LSX identity nodes by label")
    ap.add_argument("--lsx-node", metavar="NODE", help="attribute schema for an LSX node type")
    ap.add_argument("--lsx-grep", metavar="REGEX", help="raw search across the LSX corpus")
    ap.add_argument("--verify-lsx", action="store_true",
                    help="cross-check the LSX tokeniser against ElementTree")
    ap.add_argument("--identity-audit", action="store_true",
                    help="re-derive the GUID identity rule from the data")
    ap.add_argument("--skip-lsx", action="store_true",
                    help="build the stats index only (the LSX pass is the slow half)")
    args = ap.parse_args()

    # LSX queries answer from the prebuilt index and never touch the stats pass.
    if args.verify_lsx:
        return verify_lsx()
    if args.identity_audit:
        return identity_audit()
    if args.lsx_grep:
        cmd_lsx_grep(args.lsx_grep)
        return 0
    if args.uuid or args.lsx_name or args.lsx_node:
        lsx = load_lsx()
        if not lsx:
            return 1
        if args.uuid:
            cmd_uuid(lsx, args.uuid)
        if args.lsx_name:
            cmd_lsx_name(lsx, args.lsx_name)
        if args.lsx_node:
            cmd_lsx_node(lsx, args.lsx_node)
        return 0

    entries = build_index()
    if not entries:
        print(f"no entries parsed - is {UNPACKED} present?", file=sys.stderr)
        return 1
    by_name = resolve_using(entries)

    if args.find:
        cmd_find(entries, args.find)
        return 0
    if args.entry:
        cmd_entry(entries, by_name, args.entry)
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mined = mine(entries)
    mined["valuelists"] = parse_valuelists()

    slim = [
        {"name": e["name"], "type": e["type"], "file": e["file"],
         "root": e["root"], "using": e["using"]}
        for e in entries
    ]
    (OUT_DIR / "index.json").write_text(
        json.dumps(slim, indent=0), encoding="utf-8")
    (OUT_DIR / "vocabulary.json").write_text(
        json.dumps(mined, indent=1), encoding="utf-8")

    print(f'wrote {OUT_DIR / "index.json"} ({len(slim)} entries)', file=sys.stderr)
    print(f'wrote {OUT_DIR / "vocabulary.json"} '
          f'({len(mined["functors"])} functors, {len(mined["conditions"])} conditions, '
          f'{len(mined["valuelists"])} enums)', file=sys.stderr)

    if args.skip_lsx:
        print("skipped the LSX pass (--skip-lsx)", file=sys.stderr)
        return 0

    lsx = build_lsx_index()
    if lsx:
        # Separators without spaces: this file is ~35 MB and is machine-read only.
        (OUT_DIR / "lsx_index.json").write_text(
            json.dumps(lsx, separators=(",", ":")), encoding="utf-8")
        size_mb = (OUT_DIR / "lsx_index.json").stat().st_size / 1048576
        print(f'wrote {OUT_DIR / "lsx_index.json"} '
              f'({len(lsx["defs"])} defined GUIDs, {len(lsx["schema"])} node types, '
              f'{size_mb:.0f} MB)', file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
