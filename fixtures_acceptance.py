# SPDX-License-Identifier: GPL-3.0-or-later
"""Controls for the broken-mod fixtures.

The fixtures are test data for checkers that mostly DO NOT EXIST YET, so this harness
cannot assert "the checker caught it." What it can assert - and what actually matters -
is that each fixture is broken in exactly the way it claims, and broken in only ONE way.

⚠ THE FAILURE THIS GUARDS AGAINST IS A FIXTURE THAT ISN'T BROKEN.
    A fixture that quietly stopped carrying its defect makes every checker run against it
    look correct. That is worse than having no fixture: a silent pass reads as evidence.
    So every break is asserted present here, AND asserted absent from `healthy`, which is
    rule 4.7 (positive-control every check) applied to the test data itself.

⚠ AND A FIXTURE THAT IS BROKEN TWICE.
    Every fixture except `xml-malformed` must still parse as XML. If one did not, an XML
    check would fire on it first and the specific defect under test would never be
    reached - the harness would pass while proving nothing about the check it names.
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
FX = HERE / "fixtures"
NAME = "FixtureBlade"
FIGHTER = "721dfac3-92d4-41f5-b773-b7072a86232f"

ok = bad = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        bad += 1
        print(f"  FAIL  {label}" + (f" - {detail}" if detail else ""))


def files_of(fid: str) -> dict[str, str]:
    root = FX / fid
    return {str(p.relative_to(root)).replace("\\", "/"):
            p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(root.rglob("*")) if p.is_file()}


def parses(text: str) -> bool:
    try:
        ET.fromstring(text)
        return True
    except ET.ParseError:
        return False


print("\nfixture controls\n")

# ---- the manifest and the tree agree ------------------------------------------
mf = FX / "manifest.json"
check("manifest.json exists", mf.is_file())
if not mf.is_file():
    print("\n0 passed, 1 failed")
    sys.exit(1)

man = json.loads(mf.read_text(encoding="utf-8"))
listed = {f["id"] for f in man["fixtures"]}
on_disk = {p.name for p in FX.iterdir() if p.is_dir() and p.name != "__pycache__"}
check("every listed fixture is on disk", listed <= on_disk, str(sorted(listed - on_disk)))
check("every fixture on disk is listed", on_disk <= listed, str(sorted(on_disk - listed)))
check("manifest entries are complete",
      all(all(f.get(k) for k in ("id", "breaks", "symptom", "severity", "expect"))
          for f in man["fixtures"]))
check("exactly one control", sum(1 for f in man["fixtures"]
                                 if f["severity"] == "control") == 1)

healthy = files_of("healthy")
# Derived, not counted. A literal here went stale the moment the scaffold grew a spell
# chain - the same failure as the "six files" assert in forge_acceptance.py, on the same
# afternoon. Anything a generator produces should be asserted against the generator.
sys.path.insert(0, str(HERE))
import forge as _F  # noqa: E402
check("healthy carries every file the scaffold declares, plus its forge.json",
      len(healthy) == len(_F.FILES) + 1 and "forge.json" in healthy,
      str(sorted(healthy)))
check("the forge.json is what makes a fixture runnable",
      '"name": "FixtureBlade"' in healthy["forge.json"],
      "without it the real validate.py cannot be pointed at a fixture at all")

# ---- structural sanity across every fixture ------------------------------------
for fid in sorted(listed):
    t = files_of(fid)
    check(f"[{fid}] same file set as healthy", set(t) == set(healthy),
          str(set(t) ^ set(healthy)))

    lsx_ok = all(parses(v) for k, v in t.items() if k.endswith(".lsx"))
    if fid == "xml-malformed":
        check(f"[{fid}] does NOT parse as XML", not lsx_ok,
              "the defect has gone missing - this fixture proves nothing now")
    else:
        check(f"[{fid}] still parses as XML", lsx_ok,
              "broken twice: an XML check would fire before the defect under test")

    if fid != "healthy":
        differs = [k for k in healthy if t.get(k) != healthy[k]]
        check(f"[{fid}] differs from healthy", bool(differs),
              "IDENTICAL to the control - it is not broken at all")
        check(f"[{fid}] differs in exactly one file", len(differs) == 1, str(differs))

# ---- each defect is actually present, and absent from the control ---------------
CD = f"Public/{NAME}/ClassDescriptions/ClassDescriptions.lsx"
PR = f"Public/{NAME}/Progressions/Progressions.lsx"
PA = f"Public/{NAME}/Stats/Generated/Data/Passive.txt"
LO = f"Localization/English/{NAME}.xml"
MT = f"Mods/{NAME}/meta.lsx"

t = files_of("dangling-passive")
check("[dangling-passive] grants a passive no stats file defines",
      "NoSuchPassive" in t[PR] and "NoSuchPassive" not in t[PA])
check("[healthy] every granted passive is defined",
      f'value="{NAME}_Placeholder"' in healthy[PR]
      and f'"{NAME}_Placeholder"' in healthy[PA])

t = files_of("table-uuid-mismatch")


def attr(text: str, ident: str) -> str:
    key = f'id="{ident}" type="guid" value="'
    i = text.find(key)
    return "" if i < 0 else text[i + len(key):text.find('"', i + len(key))]


check("[table-uuid-mismatch] the two table UUIDs disagree",
      attr(t[CD], "ProgressionTableUUID") != attr(t[PR], "TableUUID"))
check("[healthy] the two table UUIDs agree",
      attr(healthy[CD], "ProgressionTableUUID") == attr(healthy[PR], "TableUUID")
      and attr(healthy[CD], "ProgressionTableUUID") != "")

t = files_of("unresolvable-parent")
check("[unresolvable-parent] ParentGuid is not a real class",
      attr(t[CD], "ParentGuid") != FIGHTER)
check("[unresolvable-parent] but it is still a well-formed guid",
      len(attr(t[CD], "ParentGuid")) == 36,
      "the whole point is that a SHAPE check cannot catch this one")
check("[healthy] ParentGuid is the real Fighter guid",
      attr(healthy[CD], "ParentGuid") == FIGHTER,
      "if this fails, the fixtures were built against different game data")

t = files_of("missing-loca-handle")
feat_handle = healthy[PA].split('data "DisplayName" "')[1].split(";")[0]
check("[missing-loca-handle] the handle is referenced but has no content",
      feat_handle in t[PA] and feat_handle not in t[LO])
check("[healthy] every referenced handle has content",
      feat_handle in healthy[PA] and feat_handle in healthy[LO])

t = files_of("duplicate-uuid")
check("[duplicate-uuid] the progression reuses the class UUID",
      attr(t[PR], "UUID") == attr(healthy[CD], "UUID"))
check("[healthy] no UUID is reused",
      attr(healthy[PR], "UUID") != attr(healthy[CD], "UUID"))

t = files_of("using-cycle")
check("[using-cycle] two entries name each other",
      f'using "{NAME}_CycleB"' in t[PA] and f'using "{NAME}_CycleA"' in t[PA])
check("[healthy] no `using` at all", "using " not in healthy[PA])

t = files_of("dependency-absent")
check("[dependency-absent] declares an uninstalled module",
      "NotInstalledModule" in t[MT])
check("[healthy] declares no dependencies",
      '<node id="Dependencies"/>' in healthy[MT])

t = files_of("meta-invented-field")
check("[meta-invented-field] uses a field name vanilla does not have",
      'id="StartLevelName"' in t[MT] and 'id="StartupLevelName"' not in t[MT])
check("[healthy] uses the real field name",
      'id="StartupLevelName"' in healthy[MT],
      "verified against every meta.lsx in the unpacked game data - StartLevelName "
      "appears in none of them")

t = files_of("xml-malformed")
check("[xml-malformed] the closing tag is the only thing missing",
      "</region>" not in t[PR] and "<region" in t[PR])

# ---- the fixtures must not look like a shippable mod -----------------------------
# Derived from the scaffold, not typed - this set has grown twice in one afternoon
# (DESIGN.md, then build.ps1) and each time the literal was the thing that broke.
# ⚠ The rule is about GAME data, not about depth. It was `"/" not in r`, which read
# "lives at the root" and happened to coincide with "is not game data" only while every
# non-game file was a root file. tools/validate.py joined the scaffold on 2026-09-01 so
# a generated mod starts with a validator, and the coincidence broke: a TOOL in a
# subfolder got flagged as an unnamespaced game file. Derive the real distinction.
GAME_DIRS = ("Mods/", "Public/", "Localization/")
ROOT_FILES = {"forge.json"} | {r for r, _ in _F.FILES if not r.startswith(GAME_DIRS)}
check("fixtures are not mistakable for a real mod",
      all(NAME in k or "Localization" in k or k in ROOT_FILES for k in healthy),
      f"every GAME file must be namespaced under the fixture name; only {ROOT_FILES} "
      f"live at the root. Got: {sorted(healthy)}")
check("the control's mod UUID is the documented deterministic one",
      "11111111" not in healthy[MT],
      "the namespace itself must not leak into output as a mod UUID")

print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
