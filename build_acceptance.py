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
