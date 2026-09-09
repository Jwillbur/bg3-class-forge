# SPDX-License-Identifier: GPL-3.0-or-later
"""Audit a mod's own Shapeshift/Rulebook.lsx against the shipped rulebooks.

    py forge/shapeshift_audit.py            # run in a mod directory
    py forge/shapeshift_audit.py --list     # every shipped rule and what it strips

⭐ WHY THIS EXISTS
    A `POLYMORPHED` status keeps exactly what its `Rules` resource permits, and a
    mod may ship its own rule. Nothing else in this toolkit looks at that file, and
    it is unusually easy to get silently wrong:

      * A MISSPELLED ATTRIBUTE IS IGNORED, NOT REJECTED. `DisableEquipmentSlot`
        (no s) parses fine and does nothing, so the armour stays on and the only
        symptom is a screenshot.
      * A status naming a `Rules` GUID that does not exist falls back to an
        engine default nobody has documented.
      * Oath of Avernus shipped POLYMORPHED statuses with NO `Rules` field at all
        for a day. Every other audit passed them.

⚠ WHAT IT CANNOT DO
    It cannot tell you the rule is the RIGHT one - only that every attribute name
    is one the engine reads, every referenced rule resolves, and the combination
    is not self-contradictory. Whether your capstone should keep its armour is a
    design question.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import modconfig  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ERR: list[str] = []
WARN: list[str] = []


def err(m: str) -> None:
    ERR.append(m)


def warn(m: str) -> None:
    WARN.append(m)


def shipped_rulebooks(unpacked: Path) -> list[Path]:
    return sorted(unpacked.rglob("Shapeshift/Rulebook.lsx"))


def known_templates(unpacked: Path, public: Path) -> set:
    """Every character template MapKey the game or this mod defines.

    ⛔ WHY. This audit checked that a POLYMORPHED status HAS a TemplateID and never
    that the TemplateID RESOLVES. On 2026-09-08 a live test found the male Cambion
    Form made the character DISAPPEAR COMPLETELY, and the female form was perfect -
    a template problem that every gate here passed. A GUID that resolves to nothing
    is exactly as silent, and typing one is one keystroke away.

    Reads the unpacked LSX mirror when there is one, because RootTemplates ship as
    binary .lsf and only the converted copy is greppable.
    """
    seen: set = set()
    roots = [public]
    lsx = Path(str(unpacked) + "_lsx")
    roots.append(lsx if lsx.is_dir() else unpacked)
    for root in roots:
        if not root or not Path(root).is_dir():
            continue
        for f in Path(root).rglob("RootTemplates/*.lsx"):
            try:
                text = f.read_text(encoding="utf8", errors="replace")
            except OSError:
                continue
            seen |= set(re.findall(r'id="MapKey"[^>]*value="([^"]+)"', text))
    return seen

def parse_rules(text: str) -> list[dict]:
    """Every <node id="Rule"> as {attr: value}, header attributes only."""
    out = []
    for b in re.split(r'<node id="Rule">', text)[1:]:
        head = b[:b.index("<children>")] if "<children>" in b else b
        d = {}
        for m in re.finditer(r'<attribute id="([A-Za-z_]+)"[^>]*value="([^"]*)"', head):
            d[m.group(1)] = m.group(2)
        if d:
            out.append(d)
    return out


# Attributes that decide whether the shapeshifted character keeps its own kit.
# Named here so the report can say what a rule DOES rather than list booleans.
STRIPPERS = ("RemovePrevSpells", "DisableEquipmentSlots", "WildShapeHotBar",
             "ApplySpellsFromTemplate", "BaseACOverride", "BlockLevelUp")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="print every shipped rule and what it strips, then exit")
    a = ap.parse_args()

    try:
        cfg = modconfig.load(Path.cwd())
    except Exception as e:  # noqa: BLE001
        print(f"cannot locate this mod: {e}", file=sys.stderr)
        return 2

    unpacked = cfg.unpacked

    # Does this mod have anything to check at all? Answer BEFORE demanding the
    # corpus - a mod with no polymorphs and no rulebook is not "unverified", it
    # is out of scope, and failing it would break every build that has no game
    # data (which is exactly what happened on this gate's first run).
    own_book = (cfg.public / "Shapeshift" / "Rulebook.lsx").is_file()
    has_poly = False
    if cfg.stats.is_dir():
        for f in cfg.stats.glob("*.txt"):
            if 'data "StatusType" "POLYMORPHED"' in f.read_text(
                    encoding="utf-8", errors="replace"):
                has_poly = True
                break
    if not own_book and not has_poly:
        print(f"shapeshift audit - {cfg.name}")
        print("  no POLYMORPHED statuses and no Rulebook.lsx - nothing to check.")
        return 0

    templates = known_templates(unpacked, cfg.public)
    if templates:
        print(f"  {len(templates):,} character template(s) known")
    else:
        print("  !! NO character templates could be read - TemplateID values are "
              "NOT CHECKED. That is a gap, not a pass.")
    books = shipped_rulebooks(unpacked)
    if not books:
        print(f"no shipped Rulebook.lsx under {unpacked} - cannot check anything.",
              file=sys.stderr)
        print("This is NOT a pass, it is NO RESULT. Re-unpack the game data.",
              file=sys.stderr)
        return 2

    vocab: set[str] = set()
    shipped: dict[str, dict] = {}
    for b in books:
        for r in parse_rules(b.read_text(encoding="utf-8", errors="replace")):
            vocab.update(r)
            if "UUID" in r:
                shipped[r["UUID"].lower()] = r

    if a.list:
        print(f"{len(shipped)} shipped rule(s), from {len(books)} rulebook(s)\n")
        print("%-36s %s" % ("RULE", "  ".join(s[:5] for s in STRIPPERS)))
        for u, r in sorted(shipped.items(), key=lambda kv: kv[1].get("RuleName", "")):
            on = ["Y" if r.get(s) == "true" else "." for s in STRIPPERS]
            print("%-36s %s  %s" % (r.get("RuleName", "?")[:36],
                                    "      ".join(on), u))
        return 0

    print(f"shapeshift audit - {cfg.name}")
    print(f"  vocabulary: {len(vocab)} attribute(s) across {len(shipped)} shipped rule(s)")

    ours = cfg.public / "Shapeshift" / "Rulebook.lsx"
    mine: dict[str, dict] = {}
    if ours.is_file():
        for r in parse_rules(ours.read_text(encoding="utf-8", errors="replace")):
            name = r.get("RuleName", "(unnamed)")
            if "UUID" not in r:
                err(f"rule {name!r} has no UUID - nothing can reference it")
                continue
            u = r["UUID"].lower()
            mine[u] = r
            if u in shipped:
                err(f"rule {name!r} reuses a SHIPPED rule's UUID "
                    f"({shipped[u].get('RuleName')}) - one of them will lose")
            # ⚠ the check this file exists for
            for k in r:
                if k not in vocab:
                    err(f"rule {name!r}: attribute {k!r} is not one the engine "
                        f"reads. A misspelled attribute is IGNORED, not rejected.")
            keeps = [s for s in STRIPPERS if r.get(s) == "false"]
            takes = [s for s in STRIPPERS if r.get(s) == "true"]
            print(f"  rule {name!r}  {r['UUID']}")
            print(f"      strips: {', '.join(takes) or 'nothing'}")
            print(f"      keeps : {', '.join(keeps) or '(unstated - engine default)'}")
            for s in STRIPPERS:
                if s not in r:
                    warn(f"rule {name!r} does not state {s} - the engine picks. "
                         f"State it either way so the intent is on the record.")
    else:
        print(f"  no {ours.relative_to(cfg.root).as_posix()} - this mod ships no rule")

    # --- every POLYMORPHED status must name a rule that exists -----------------
    n_poly = 0
    for f in sorted((cfg.stats).glob("*.txt")):
        cur = None
        entries: dict[str, dict] = {}
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            m = re.match(r'^new entry "([^"]+)"', line)
            if m:
                cur = {}
                entries[m.group(1)] = cur
                continue
            m = re.match(r'^data "([^"]+)"\s+"(.*)"\s*$', line)
            if m and cur is not None:
                cur[m.group(1)] = m.group(2)
        for name, e in entries.items():
            if e.get("StatusType") != "POLYMORPHED":
                continue
            n_poly += 1
            rules = e.get("Rules")
            if not rules:
                err(f"{name}: POLYMORPHED with NO `Rules` field. The engine falls "
                    f"back to an undocumented default - it is not 'keeps everything'.")
            elif rules.lower() not in shipped and rules.lower() not in mine:
                err(f"{name}: Rules {rules} is neither shipped nor defined by this "
                    f"mod. The status will not find it.")
            tid = e.get("TemplateID")
            if not tid:
                err(f"{name}: POLYMORPHED with no TemplateID - nothing to become.")
            elif templates and tid.strip() not in templates:
                err(f"{name}: TemplateID {tid} resolves to no character template, "
                    f"ours or shipped. The transformation has nothing to become and "
                    f"the character can simply vanish.")
            if "SG_Polymorph" not in (e.get("StatusGroups") or ""):
                warn(f"{name}: StatusGroups omits SG_Polymorph. Dispels, dialogue "
                     f"drops and any `HasStatus('SG_Polymorph')` gate will miss it.")

    print(f"  {n_poly} POLYMORPHED status(es) checked")
    print()
    for m in ERR:
        print(f"ERROR  {m}")
    for m in WARN:
        print(f"WARN   {m}")
    print(f"\n{len(ERR)} error(s), {len(WARN)} warning(s)")
    if not ERR:
        print("every rule attribute is one the engine reads, and every Rules "
              "reference resolves.")
    return 1 if ERR else 0


if __name__ == "__main__":
    raise SystemExit(main())
