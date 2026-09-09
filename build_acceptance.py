"""Positive-control build.ps1 itself, with fake Divine executables.

WHY. Every harness in this repo tests a Python tool. NOTHING tested the build
script, and the build script is where the mod actually gets made. An outside
review (2026-08-25) put it plainly: the acceptance suite is strong on generator
and fixture shape and "does not exercise build.ps1", so the failure it found -
Vortex's divine.exe printing [FATAL], EXITING 0, and the build packing a mod with
no compiled localisation - could not have been caught by anything here.

⭐ THE POINT IS THE NEGATIVE CONTROLS. The loca hardening was written and shipped
with only a passing build behind it, which proves the good path and says nothing
about the reject paths. A gate that has only ever said "clean" is indistinguishable
from one that cannot fail; this repo has shipped that mistake three times.

HOW THE FAKES WORK. Each fake Divine is a .cmd shim over divine_impl.py, and each
misbehaves ONLY on the real workspace file - it converts the probe's synthetic file
correctly. That is deliberate: a Divine that fails the probe is rejected before the
mod is touched (one control covers that), and the artifact checks can only be
exercised by one that gets PAST the probe and then produces a bad file. That is
also exactly the real-world shape, since Vortex's divine handles some actions and
not others.

    py forge/build_acceptance.py            # run the controls
    py forge/build_acceptance.py --help
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build.ps1"

DIVINE_IMPL = r'''
import os, sys, time
a = {}
args = sys.argv[1:]
for i, tok in enumerate(args):
    if tok in ("-a", "-s", "-d", "-g") and i + 1 < len(args):
        a[tok] = args[i + 1]
mode = os.environ.get("FORGE_FAKE_DIVINE_MODE", "good")
action, src, dst = a.get("-a"), a.get("-s"), a.get("-d")

# The probe converts a synthetic file in TEMP. Every mode except always_fatal
# handles it correctly, so the misbehaviour lands on the real mod file - the
# shape that actually shipped a broken pak.
is_probe = bool(src) and "forge_probe_" in os.path.basename(src)

if action == "convert-loca":
    if mode == "always_fatal":
        print("[FATAL] Value convert-loca is not allowed for argument a(action)")
        sys.exit(0)
    if is_probe or mode == "good":
        open(dst, "wb").write(b"LOCA\x00fake")
        sys.exit(0)
    if mode == "fatal_zero":
        print("[FATAL] Value convert-loca is not allowed for argument a(action)")
        sys.exit(0)
    if mode == "missing":
        sys.exit(0)
    if mode == "empty":
        open(dst, "wb").write(b"")
        sys.exit(0)
    if mode == "old":
        open(dst, "wb").write(b"LOCA\x00stale")
        old = time.time() - 86400
        os.utime(dst, (old, old))
        sys.exit(0)
    sys.exit(0)

if action == "create-package":
    open(dst, "wb").write(b"PAKFAKE")
    sys.exit(0)

if action == "list-package":
    print("Mods/Fixture/meta.lsx")
    print("Public/Fixture/Stats/Generated/Data/Passive.txt")
    print("Localization/English/Fixture.loca")
    sys.exit(0)

sys.exit(0)
'''

META = """<?xml version="1.0" encoding="utf-8"?>
<save><region id="Config"><node id="root"><children>
<node id="ModuleInfo"><attribute id="Name" type="LSString" value="Fixture"/>
<attribute id="UUID" type="guid" value="11111111-2222-3333-4444-555555555555"/>
</node></children></node></region></save>
"""

LOCA_XML = """<?xml version="1.0" encoding="utf-8"?>
<contentList><content contentuid="h11111111">Fixture</content></contentList>
"""

PASSIVE = 'new entry "Fixture_Passive"\ntype "PassiveData"\ndata "DisplayName" "h11111111"\n'

PASSING_VALIDATOR = "import sys\nprint('fixture validator: ok')\nsys.exit(0)\n"


def make_workspace(root: Path, with_validator: bool = True) -> Path:
    ws = root / "ws"
    (ws / "Mods" / "Fixture").mkdir(parents=True)
    (ws / "Public" / "Fixture" / "Stats" / "Generated" / "Data").mkdir(parents=True)
    (ws / "Localization" / "English").mkdir(parents=True)
    (ws / "tools").mkdir()
    (ws / "forge.json").write_text(json.dumps({"name": "Fixture"}), encoding="utf-8")
    (ws / "Mods" / "Fixture" / "meta.lsx").write_text(META, encoding="utf-8")
    (ws / "Localization" / "English" / "Fixture.xml").write_text(LOCA_XML, encoding="utf-8")
    (ws / "Public" / "Fixture" / "Stats" / "Generated" / "Data" / "Passive.txt").write_text(
        PASSIVE, encoding="utf-8")
    if with_validator:
        (ws / "tools" / "validate.py").write_text(PASSING_VALIDATOR, encoding="utf-8")
    return ws


def make_divine(root: Path, mode: str) -> Path:
    impl = root / "divine_impl.py"
    impl.write_text(DIVINE_IMPL, encoding="utf-8")
    cmd = root / ("divine_%s.cmd" % mode)
    cmd.write_text(
        "@echo off\r\n"
        "set FORGE_FAKE_DIVINE_MODE=%s\r\n"
        'py "%%~dp0divine_impl.py" %%*\r\n' % mode,
        encoding="utf-8")
    return cmd


def run_build(ws: Path, divine: Path, extra: list | None = None) -> tuple:
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BUILD),
           "-Workspace", str(ws), "-DivinePath", str(divine),
           "-SkipSelfTest", "-SkipDeploy"] + (extra or [])
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0

    fails, checks = [], 0

    def ck(name, cond, detail=""):
        nonlocal checks
        checks += 1
        if not cond:
            fails.append(name + ((" -- " + detail[-400:]) if detail else ""))

    root = Path(tempfile.mkdtemp(prefix="build_acc_"))
    try:
        # --- the good path must actually pass, or every negative below is vacuous ---
        ws = make_workspace(root)
        good = make_divine(root, "good")
        rc, out = run_build(ws, good)
        ck("a good Divine builds clean", rc == 0, out)
        ck("the good build compiled a .loca",
           (ws / "Localization" / "English" / "Fixture.loca").exists(), out)
        ck("the good build wrote validation provenance",
           (ws / "dist" / "validation-report.json").exists(), out)
        rp = ws / "dist" / "validation-report.json"
        if rp.exists():
            # Strict utf-8, no BOM tolerance: PowerShell 5.1's `Set-Content -Encoding
            # utf8` emits a BOM, and a BOM makes json.loads throw on byte zero. The
            # report exists to be machine-read, so this control must stay strict.
            ck("the report is BOM-less UTF-8", not rp.read_bytes().startswith(b"\xef\xbb\xbf"))
            rep = json.loads(rp.read_text(encoding="utf-8"))
            ck("provenance records the loca gate",
               rep.get("checks", {}).get("loca_compiled") == "pass", json.dumps(rep))
            ck("provenance names the Divine that was used",
               "divine_good" in (rep.get("divine") or ""), json.dumps(rep))
            ck("provenance refuses to imply full coverage",
               "NOT tracked" in (rep.get("note") or ""), json.dumps(rep))

        # --- negative controls: every one must REFUSE ---------------------------
        for mode, phrase, why in [
            ("always_fatal", "cannot convert localisation",
             "a Divine that cannot convert-loca at all is rejected by the probe, and an "
             "explicitly named one is NOT quietly swapped for a working one"),
            ("fatal_zero", "[FATAL]",
             "THE SHIPPED BUG: prints [FATAL], exits 0, writes nothing"),
            ("missing", "produced no .loca",
             "silent success with no artifact"),
            ("empty", "EMPTY .loca",
             "a zero-byte artifact is not a conversion"),
            ("old", "stale artifact",
             "a leftover .loca from a previous build must not pass as fresh"),
        ]:
            ws2 = make_workspace(Path(tempfile.mkdtemp(prefix="build_acc_", dir=root)))
            bad = make_divine(root, mode)
            rc, out = run_build(ws2, bad)
            ck("REFUSES %s (%s)" % (mode, why), rc != 0, out[-600:])
            ck("...and says why: %s" % mode, phrase in out, out[-600:])
            ck("...and ships no pak: %s" % mode,
               not list((ws2 / "dist").glob("*.pak")) if (ws2 / "dist").exists() else True)

        # --- the validator gate --------------------------------------------------
        ws3 = make_workspace(Path(tempfile.mkdtemp(prefix="build_acc_", dir=root)),
                             with_validator=False)
        rc, out = run_build(ws3, good)
        ck("a missing validator FAILS the build (it used to print 'skipping')",
           rc != 0, out[-600:])
        ck("...and the message names the override", "-AllowMissingValidator" in out, out[-600:])

        ws4 = make_workspace(Path(tempfile.mkdtemp(prefix="build_acc_", dir=root)),
                             with_validator=False)
        rc, out = run_build(ws4, good, ["-AllowMissingValidator"])
        ck("-AllowMissingValidator lets it through", rc == 0, out[-600:])
        if (ws4 / "dist" / "validation-report.json").exists():
            rep = json.loads((ws4 / "dist" / "validation-report.json").read_text(encoding="utf-8"))
            ck("a skipped validator is recorded as SKIPPED, not omitted",
               rep.get("checks", {}).get("project_validate_py") == "skipped", json.dumps(rep))

        # --- gate 0g: a field the engine does not read on THIS entry type ----------
        # AVERNUS_ZARIELS_FAVOR shipped as a decorative icon on 2026-09-08 carrying
        # StatsFunctorContext/Conditions/StatsFunctors on a StatusData - 678/647/785
        # shipped uses on PassiveData, ZERO on StatusData. Every other gate passed it:
        # the name is real, the syntax parses, validate.py is happy, and the feature
        # simply never runs. These controls exist because that is invisible otherwise.
        ws5 = make_workspace(Path(tempfile.mkdtemp(prefix="build_acc_", dir=root)))
        rc, out = run_build(ws5, good)
        ck("gate 0g runs on a clean workspace", "[0g/6]" in out, out[-600:])
        clean_ok = rc == 0
        ck("...and a clean workspace still builds", clean_ok, out[-600:])

        # THE FAULT. StatsFunctorContext is real, spelled right, and attested 678
        # times - just never on a StatusData. Nothing but 0g can see this.
        # Clear dist first. The clean build above already produced a pak, so without
        # this the "ships no pak" control passes on a LEFTOVER and would never fail.
        shutil.rmtree(ws5 / "dist", ignore_errors=True)
        d5 = ws5 / "Public" / "Fixture" / "Stats" / "Generated" / "Data"
        (d5 / "Status_BOOST.txt").write_text(
            'new entry "Fixture_Status"\ntype "StatusData"\n'
            'data "StatusType" "BOOST"\ndata "DisplayName" "h11111111"\n'
            'data "StatsFunctorContext" "OnDamage"\n', encoding="utf-8")
        rc, out = run_build(ws5, good)
        ck("gate 0g REFUSES a field on the wrong entry type", rc != 0, out[-800:])
        ck("...and names the field", "StatsFunctorContext" in out, out[-800:])
        ck("...and says where it DOES live", "PassiveData" in out, out[-800:])
        ck("...and ships no pak", not (ws5 / "dist" / "Fixture.pak").exists(), out[-400:])

        # THE FAILURE MODE THIS HARNESS IS FOR. Gate 0f broke on its first run by
        # treating "could not check" as "found a fault". 2 must WARN, never block -
        # otherwise the gate wedges every build on a machine with no unpacked game
        # data, and the fix people reach for is to delete the gate. Fault-inject an
        # audit that reports it could not look.
        (d5 / "Status_BOOST.txt").unlink()
        stub = Path(BUILD).parent / "wrongtype_audit.py"
        keep = stub.read_bytes()
        try:
            stub.write_text("import sys\nprint('fixture: corpus absent')\nsys.exit(2)\n",
                            encoding="utf-8")
            rc, out = run_build(ws5, good)
            ck("exit 2 does NOT block the build (could-not-check != fault)",
               rc == 0, out[-800:])
            ck("...and exit 2 is reported as NOT CHECKED, not as a pass",
               "NOT CHECKED" in out and "This is not a pass" in out, out[-800:])
        finally:
            stub.write_bytes(keep)

        # A gate that reads the caller's shell is not a gate. build.ps1 set no working
        # directory, so the python gates located the mod from wherever the caller
        # stood: the same tree passed from inside the mod folder and failed from the
        # Toolkit root with "cannot locate this mod". Found 2026-09-08 after two runs
        # of an UNCHANGED tree disagreed, and it was blamed on a stale cache first.
        # The fixture's own validator reports the directory it was RUN IN. That is the
        # behaviour under test - not what build.ps1's source says, which would prove
        # only that the config declares an intention. A first draft of this control ran
        # the build from an unrelated cwd and asserted rc == 0; it PASSED against a
        # mutant with the Push-Location removed, because nothing in the fixture cared
        # where it stood. A control that survives its own mutation is not a control.
        (ws5 / "tools" / "validate.py").write_text(
            "import os, sys" + chr(10) +
            "print('fixture validator cwd=' + os.getcwd())" + chr(10) +
            "sys.exit(0)" + chr(10), encoding="utf-8")
        elsewhere = Path(tempfile.mkdtemp(prefix="build_acc_cwd_", dir=root))
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BUILD),
               "-Workspace", str(ws5), "-DivinePath", str(good),
               "-SkipSelfTest", "-SkipDeploy"]
        pr = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                            timeout=300, cwd=str(elsewhere))
        cwd_out = (pr.stdout or "") + (pr.stderr or "")
        ck("the build does not depend on the caller's working directory",
           pr.returncode == 0 and clean_ok, cwd_out[-800:])
        ck("...and the gates RAN IN the workspace, not the caller's directory",
           ("fixture validator cwd=" + str(ws5)) in cwd_out, cwd_out[-800:])

        # --- gate 0h: unattested values, dangling names, wrong carrier -------------
        # Every shape here is a bug a LIVE TEST found on 2026-09-08 after every static
        # gate in this file had passed the mod. The six spell lists used `Comment`,
        # which appears 0 times in 358 shipped SpellList nodes; seven passives named no
        # Icon and drew blank squares. Both parse, both validate, both are ignored.
        ws6 = make_workspace(Path(tempfile.mkdtemp(prefix="build_acc_", dir=root)))
        rc, out = run_build(ws6, good)
        ck("gate 0h runs on a clean workspace", "[0h/6]" in out, out[-600:])
        ck("...and a clean workspace still builds", rc == 0, out[-600:])
        d6 = ws6 / "Public" / "Fixture" / "Stats" / "Generated" / "Data"

        # CLASS 1: a Properties flag attested nowhere. NOT an Icon - an icon is a
        # global namespace and a mod may declare its own in a binary atlas nothing here
        # can read, so 0h reports icons as a NOTE and never blocks on one. This control
        # faulted a bogus ICON at first and went green against a gate that had stopped
        # blocking it: a control must fault the thing the gate actually refuses.
        shutil.rmtree(ws6 / "dist", ignore_errors=True)
        (d6 / "Passive.txt").write_text(
            'new entry "Fixture_Passive"\ntype "PassiveData"\n'
            'data "DisplayName" "h11111111"\n'
            'data "Properties" "NoSuchPropertyAnywhere"\n', encoding="utf-8")
        rc, out = run_build(ws6, good)
        ck("gate 0h REFUSES a value attested nowhere", rc != 0, out[-800:])
        ck("...and names the value", "NoSuchPropertyAnywhere" in out, out[-800:])
        ck("...and ships no pak", not (ws6 / "dist" / "Fixture.pak").exists(), out[-400:])

        # CLASS 2: a functor naming a status that is in neither the corpus nor the mod.
        shutil.rmtree(ws6 / "dist", ignore_errors=True)
        (d6 / "Passive.txt").write_text(
            'new entry "Fixture_Passive"\ntype "PassiveData"\n'
            'data "DisplayName" "h11111111"\n'
            'data "StatsFunctorContext" "OnDamage"\n'
            'data "StatsFunctors" "ApplyStatus(SELF, FIXTURE_NO_SUCH_STATUS, 100, 1)"\n',
            encoding="utf-8")
        rc, out = run_build(ws6, good)
        ck("gate 0h REFUSES an identifier that resolves nowhere", rc != 0, out[-800:])
        ck("...and names the identifier", "FIXTURE_NO_SUCH_STATUS" in out, out[-800:])

        # The exit split again. 0f broke by treating "could not check" as "found a
        # fault", which wedges every build on a machine with no unpacked game data -
        # and the fix people reach for is to delete the gate.
        (d6 / "Passive.txt").write_text(PASSIVE, encoding="utf-8")
        stub = Path(BUILD).parent / "class_sweep.py"
        keep = stub.read_bytes()
        try:
            stub.write_text("import sys\nprint('fixture: corpus absent')\nsys.exit(2)\n",
                            encoding="utf-8")
            rc, out = run_build(ws6, good)
            ck("0h exit 2 does NOT block the build", rc == 0, out[-800:])
            ck("...and 0h exit 2 reads as NOT CHECKED, not as a pass",
               "NOT CHECKED" in out and "This is not a pass" in out, out[-800:])
        finally:
            stub.write_bytes(keep)

        # --- gate 0i: a real function in the wrong context --------------------------
        # The fault below is a VERBATIM REPLAY of a bug that shipped on 2026-09-08.
        # SourceSpellDC() has 377 uses and is perfectly real; in a weapon-triggered
        # OnDamage passive there is no source spell to read, so the save auto-failed
        # and the effect landed every time. Zero shipped uses in that context.
        ws7 = make_workspace(Path(tempfile.mkdtemp(prefix="build_acc_", dir=root)))
        rc, out = run_build(ws7, good)
        ck("gate 0i runs on a clean workspace", "[0i/6]" in out, out[-600:])
        ck("...and a clean workspace still builds", rc == 0, out[-600:])
        d7 = ws7 / "Public" / "Fixture" / "Stats" / "Generated" / "Data"
        shutil.rmtree(ws7 / "dist", ignore_errors=True)
        (d7 / "Passive.txt").write_text(
            'new entry "Fixture_Passive"\ntype "PassiveData"\n'
            'data "DisplayName" "h11111111"\n'
            'data "StatsFunctorContext" "OnDamage"\n'
            'data "StatsFunctors" "ApplyStatus(BURNING,100,2,,,,not SavingThrow(Ability.Constitution,SourceSpellDC()))"\n',
            encoding="utf-8")
        rc, out = run_build(ws7, good)
        ck("gate 0i REFUSES a function used in the wrong context", rc != 0, out[-900:])
        ck("...and names the function", "SourceSpellDC" in out, out[-900:])
        ck("...and names the context it is absent from", "OnDamage" in out, out[-900:])
        ck("...and ships no pak", not (ws7 / "dist" / "Fixture.pak").exists(), out[-400:])

        # ⛔ THE SAME FAULT WITH THE FIELDS IN THE OTHER ORDER. context_audit read each
        # field as it went, so `StatsFunctors` written ABOVE `StatsFunctorContext` was
        # measured with no context and the whole layer skipped it - silently, and it
        # skipped LEARNING those vocabularies too, so 55 of them did not exist. Field
        # order in a stats entry is arbitrary and this control keeps it that way.
        shutil.rmtree(ws7 / "dist", ignore_errors=True)
        (d7 / "Passive.txt").write_text(
            'new entry "Fixture_Passive"\ntype "PassiveData"\n'
            'data "DisplayName" "h11111111"\n'
            'data "StatsFunctors" "ApplyStatus(BURNING,100,2,,,,not SavingThrow(Ability.Constitution,SourceSpellDC()))"\n'
            'data "StatsFunctorContext" "OnDamage"\n',
            encoding="utf-8")
        rc, out = run_build(ws7, good)
        ck("gate 0i catches it with the context declared BELOW the field", rc != 0,
           out[-900:])
        ck("...and still names the function", "SourceSpellDC" in out, out[-900:])
        # The exit split, a third time. 0f broke by treating "could not check" as
        # "found a fault", which wedges every build on a machine with no game data.
        (d7 / "Passive.txt").write_text(PASSIVE, encoding="utf-8")
        stub = Path(BUILD).parent / "context_audit.py"
        keep = stub.read_bytes()
        try:
            stub.write_text("import sys\nprint('fixture: corpus absent')\nsys.exit(2)\n",
                            encoding="utf-8")
            rc, out = run_build(ws7, good)
            ck("0i exit 2 does NOT block the build", rc == 0, out[-800:])
            ck("...and 0i exit 2 reads as NOT CHECKED, not as a pass",
               "NOT CHECKED" in out and "This is not a pass" in out, out[-800:])
        finally:
            stub.write_bytes(keep)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ⚠ THE SUMMARY LINE IS AN INTERFACE, not decoration. selftest.py parses it, and
    # the first version printed "N checks, M failed" - which the runner could not read,
    # so it recorded "exited clean but reported no checks at all". A harness the runner
    # cannot count is a harness that silently stops guarding anything the moment it is
    # added to the suite. Match the shape every other harness prints.
    print("%d passed, %d failed" % (checks - len(fails), len(fails)))
    for f in fails:
        print("  FAIL " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
