"""Does every name this mod REFERENCES actually exist?

⚠ WHY THIS EXISTS

    Nothing else asked. `validate.py` checks shape, `loca_lint.py` checks handles,
    `fx_audit.py` checks effects and sounds, `inherit_audit.py` checks inherited
    fields — and none of them check that `ApplyStatus(SELF,WARPBLADE_FOO,100,1)`
    names a status that is defined anywhere. In BG3 a dangling name is usually a
    silent no-op: the spell casts, the status never lands, and nothing is logged.
    That is the worst kind of bug to own, because the mod looks like it works.

    Written during a full mod audit on 2026-08-29. The mod came back CLEAN — 27
    referenced names, all resolving against 43 of ours plus 16,132 vanilla. It is
    a tool, not a fix, and that is the point: a one-off proof decays the moment
    the session ends, which is the lesson this whole repo keeps relearning.

⚠ STATUS GROUPS ARE NOT STATS ENTRIES

    The first run of this reported `SG_Polymorph` as dangling. It is not — `SG_`
    names a StatusGroup, which lives in the valuelists, not in the stats index.
    Reporting it would have sent someone hunting a bug in working code, so the
    valuelists are loaded and unioned in. **A checker that cries wolf on a whole
    naming convention gets switched off within a week.**
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus_index as ci                                   # noqa: E402

# --- mod config -----------------------------------------------------------
# Anchored on the MOD BEING AUDITED (the cwd), not on this file. Until
# 2026-09-06 every path below was the literal string CFG.name, so running
# this from another mod silently audited Warpblade instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402
CFG = modconfig.load(Path.cwd())


DATA = CFG.stats \
    if hasattr(ci, "CFG") and hasattr(ci.CFG, "root") \
    else Path(str(CFG.public / "Stats/Generated/Data"))

# Functors and conditions that name a status, passive or spell as an argument.
PATTERNS = (
    r"ApplyStatus\(\s*(?:[A-Z_]+\s*,\s*)?([A-Z][A-Z0-9_]{2,})",
    r"RemoveStatus\(\s*(?:[A-Z_]+\s*,\s*)?([A-Z][A-Z0-9_]{2,})",
    r"HasStatus\('([^']+)'",
    r"HasPassive\('([^']+)'",
    r"UnlockSpell\(([A-Za-z_][\w]*)",
    r"AddSpell\(([A-Za-z_][\w]*)",
    r"UnlockSpellVariant\(\s*SpellId\('([^']+)'",
)

# Context keywords, not names. These are the functor's TARGET argument.
KEYWORD = re.compile(r"^(SELF|SWAP|OBSERVER|SOURCE|TARGET|CONTEXT|OWNER|CAUSE|"
                     r"ENTITY|ITEM|OBSERVER_OBSERVER|OBSERVER_SOURCE|[0-9]+)$")


def known_names(data_dir: Path) -> tuple[dict, set]:
    ours = {}
    for f in sorted(data_dir.glob("*.txt")):
        for e in ci.parse_file(f):
            ours[e["name"]] = e

    vanilla = ci.build_index()
    if isinstance(vanilla, list):
        vanilla = {e["name"]: e for e in vanilla}

    # ⚠ StatusGroups live here, not in the stats index. Without them every
    #   SG_* reference reads as dangling - see the module docstring.
    groups: set[str] = set()
    try:
        vl = ci.parse_valuelists()
        if isinstance(vl, dict):
            for v in vl.values():
                if isinstance(v, (list, set, tuple)):
                    groups |= {str(x) for x in v}
    except Exception as e:                                  # noqa: BLE001
        print(f"! valuelists unreadable ({e}) - StatusGroup names will read as "
              f"dangling. Fix this before believing any SG_* finding.",
              file=sys.stderr)

    return ours, set(ours) | set(vanilla) | groups


def references(ours: dict) -> dict[str, set]:
    refs: dict[str, set] = defaultdict(set)
    for name, e in ours.items():
        blob = "\n".join(str(v) for v in e.get("data", {}).values())
        for pat in PATTERNS:
            for m in re.finditer(pat, blob):
                refs[m.group(1)].add(name)
        if e.get("using"):
            refs[e["using"]].add(name + " (using)")
    return refs


# ⛔ Equipment.txt was invisible to this tool until 2026-09-08. It sits one directory
# ABOVE Data/ and has its own grammar - `new equipment "X"` defines a set,
# `add equipment entry "Y"` names an item inside one - so the stats parser never saw it
# and every item name in it went unchecked. Oath of Avernus shipped nine of them the day
# its character-creation kit was written, and the tool still said "clean".
# A wrong name here is silent in exactly the way this whole file exists to catch: the
# character simply starts without that item.
EQP_DEF = re.compile(r'^new equipment "([^"]+)"', re.M)
EQP_REF = re.compile(r'^add equipment entry "([^"]+)"', re.M)


def equipment_names(ours_stats: set) -> tuple[dict, set]:
    """(item name -> the equipment sets naming it), and every defined set name."""
    refs: dict[str, set] = defaultdict(set)
    defined: set = set()

    def scan(path: Path, collect_refs: bool):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        cur = None
        for line in text.splitlines():
            m = EQP_DEF.match(line)
            if m:
                cur = m.group(1); defined.add(cur); continue
            m = EQP_REF.match(line)
            if m and collect_refs:
                refs[m.group(1)].add(cur or path.name)

    for p in sorted(CFG.public.glob("*/Stats/Generated/Equipment.txt")):
        scan(p, True)
    scan(Path(str(CFG.public / "Stats/Generated/Equipment.txt")), True)
    for p in sorted(Path(ci.UNPACKED).glob("*/Public/*/Stats/Generated/Equipment.txt")
                    if hasattr(ci, "UNPACKED") else []):
        scan(p, False)
    return refs, defined


def class_equipment_refs() -> dict[str, set]:
    """`ClassEquipment` in a ClassDescription must name a defined equipment SET.
    Ours named none at all until v1.0.2.7, which is why the character started naked."""
    out: dict[str, set] = defaultdict(set)
    for p in CFG.public.rglob("ClassDescriptions/*.lsx"):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'id="ClassEquipment"[^>]*value="([^"]+)"', text):
            out[m.group(1)].add(p.name)
    return out


def audit(data_dir: Path | None = None) -> tuple[dict, int, int]:
    d = data_dir or DATA
    ours, known = known_names(d)
    refs = references(ours)

    eq_refs, eq_defined = equipment_names(known)
    for item, users in eq_refs.items():
        refs[item] |= {u + " (equipment)" for u in users}
    for setname, users in class_equipment_refs().items():
        refs[setname] |= {u + " (ClassEquipment)" for u in users}
    known = known | eq_defined

    missing = {n: v for n, v in refs.items()
               if n not in known and not KEYWORD.match(n)}
    return missing, len(refs), len(known)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", metavar="DIR", help="stats directory to audit")
    a = ap.parse_args()

    missing, n_refs, n_known = audit(Path(a.data) if a.data else None)

    # ⭐ COVERAGE IN THE VERDICT. "0 dangling" over 0 references parsed is not a
    #   clean result, it is no result - the bug class this repo found five times
    #   on the day this tool was written.
    if n_refs == 0:
        print("⚠ NOTHING WAS CHECKED - 0 references parsed. That is not clean, it "
              "is empty.\n  Check the stats directory and forge.json.")
        return 2

    print(f"{n_refs} referenced name(s) checked against {n_known:,} known entries")
    if not missing:
        print("clean - every name this mod references is defined, ours or vanilla.")
        return 0

    print(f"\n{len(missing)} NAME(S) REFERENCED BUT NOT DEFINED:")
    for n, users in sorted(missing.items()):
        print(f"  {n:<44} <- {', '.join(sorted(users))[:60]}")
    print("\nIn BG3 a dangling name is usually a SILENT no-op, not an error. The "
          "spell will\ncast and the effect will simply never happen.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
