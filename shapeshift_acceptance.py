# SPDX-License-Identifier: GPL-3.0-or-later
"""Fault-inject shapeshift_audit.py: break the rulebook on purpose, demand a failure.

    py forge/shapeshift_acceptance.py

⭐ WHY. `shapeshift_audit.py` passed the first mod it was ever pointed at. That is
   the least informative result a new gate can produce, and this repo has been
   burned by it twice - `fx_audit` did not run inside a build for weeks, and
   `anim_textkeys` had to be fault-injected before anyone believed it. A check that
   has never failed has never been tested.

   Each case below copies the real mod to a temp directory, breaks ONE thing, and
   asserts the audit exits non-zero with the expected message.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD = HERE.parent / "bg3" / "OathOfAvernus"
AUDIT = HERE / "shapeshift_audit.py"

OK = True


def check(label: str, got, want=True) -> None:
    global OK
    good = got == want
    if not good:
        OK = False
    print("  %s  %s%s" % ("PASS" if good else "FAIL", label,
                          "" if good else "   got=%r want=%r" % (got, want)))


def run(root: Path) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(AUDIT)], cwd=str(root),
                       capture_output=True, text=True, errors="replace")
    return p.returncode, p.stdout + p.stderr


def sandbox() -> Path:
    d = Path(tempfile.mkdtemp(prefix="shapeshift_"))
    dst = d / "OathOfAvernus"
    shutil.copytree(MOD, dst, ignore=shutil.ignore_patterns(
        "dist", "obj", "__pycache__", "*.pak", "corpus"))
    return dst


def rulebook(root: Path) -> Path:
    return root / "Public/OathOfAvernus/Shapeshift/Rulebook.lsx"


def status(root: Path) -> Path:
    return root / "Public/OathOfAvernus/Stats/Generated/Data/Status_BOOST.txt"


if not MOD.is_dir():
    print("no Oath of Avernus workspace to copy - skipping")
    raise SystemExit(0)

print("shapeshift_audit fault injection\n")

# --- 0. the control: unmodified must PASS ----------------------------------
root = sandbox()
rc, out = run(root)
check("the unmodified mod passes", rc, 0)
check("...and says so", "0 error(s)" in out)

# --- 1. a misspelled attribute is the failure this file exists for ---------
# The engine IGNORES an attribute it does not know. No error, no log line, and
# the armour simply stays on.
root = sandbox()
p = rulebook(root)
p.write_text(p.read_text(encoding="utf8").replace(
    'id="DisableEquipmentSlots"', 'id="DisableEquipmentSlot"'), encoding="utf8")
rc, out = run(root)
check("a misspelled attribute FAILS", rc, 1)
check("...and names the attribute", "DisableEquipmentSlot'" in out)
check("...and says a typo is ignored, not rejected", "IGNORED, not rejected" in out)

# --- 2. a POLYMORPHED status with no Rules at all --------------------------
# This is exactly what Oath of Avernus shipped for a day.
root = sandbox()
p = status(root)
t = p.read_text(encoding="utf8")
t = re.sub(r'^data "Rules" "[^"]*"\n', "", t, flags=re.M)
p.write_text(t, encoding="utf8")
rc, out = run(root)
check("a POLYMORPHED status with NO Rules FAILS", rc, 1)
check("...and refuses to call the default 'keeps everything'",
      "undocumented default" in out)

# --- 3. a Rules GUID that resolves to nothing ------------------------------
root = sandbox()
p = status(root)
p.write_text(p.read_text(encoding="utf8").replace(
    "a7f4c1d2-3b96-4e58-9c07-2d5e81ab6f30",
    "deadbeef-0000-0000-0000-000000000000"), encoding="utf8")
rc, out = run(root)
check("a dangling Rules reference FAILS", rc, 1)
check("...and says it is neither shipped nor ours", "neither shipped nor" in out)

# --- 4. reusing a shipped rule's UUID --------------------------------------
# c7c3381e is DisguiseKeepName. Redefining it would shadow a rule the base game
# uses for Disguise Self.
root = sandbox()
p = rulebook(root)
p.write_text(p.read_text(encoding="utf8").replace(
    "a7f4c1d2-3b96-4e58-9c07-2d5e81ab6f30",
    "c7c3381e-b901-416e-a0c4-bc745e1ff54a"), encoding="utf8")
rc, out = run(root)
check("colliding with a SHIPPED rule UUID FAILS", rc, 1)
check("...and names the rule it collides with", "DisguiseKeepName" in out)

# --- 5. dropping SG_Polymorph is a warning, not an error -------------------
root = sandbox()
p = status(root)
p.write_text(p.read_text(encoding="utf8").replace(
    "SG_Polymorph;SG_DropForNonMutingDialog;SG_RemoveOnRespec",
    "SG_RemoveOnRespec"), encoding="utf8")
rc, out = run(root)
check("dropping SG_Polymorph warns", "omits SG_Polymorph" in out)
check("...but does not block the build", rc, 0)

# --- 6. a TemplateID-less polymorph ----------------------------------------
root = sandbox()
p = status(root)
t = p.read_text(encoding="utf8")
# GUID-agnostic on purpose. This was pinned to "45df7c10..." and silently stripped
# NOTHING the day the mod repointed its TemplateID at its own RootTemplates, so the
# control ran against a fixture that still HAD one and reported the gate broken when
# the gate was fine. A fixture that stops injecting its fault is worse than no
# control: it accuses working code.
t2 = re.sub(r'^data "TemplateID" "[^"]*"\n', "", t, flags=re.M)
assert t2 != t, "fixture injected nothing - there was no TemplateID line to strip"
t = t2
p.write_text(t, encoding="utf8")
rc, out = run(root)
check("a POLYMORPHED status with no TemplateID FAILS", rc, 1)
check("...and says there is nothing to become", "nothing to become" in out)

print("\n" + ("ALL GREEN" if OK else "SOMETHING FAILED"))
raise SystemExit(0 if OK else 1)
