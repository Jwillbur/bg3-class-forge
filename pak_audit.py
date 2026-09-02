# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the BUILT PAK against the source it claims to be built from.

    py tools/pak_audit.py              # audit dist/ and the deployed copy
    py tools/pak_audit.py --deployed   # only the pak the game actually loads
    py tools/pak_audit.py --json

⭐ WHY THIS EXISTS
    Every other gate in this toolkit reads the SOURCE. `validate.py`, `ref_closure`,
    `loca_lint`, `fx_audit`, `tooltip_audit`, `sim` - all of them audit files in the
    workspace. **Nothing audits the artifact the game actually opens.**

    The build does print its archive listing, at step 5, and nothing consumes it. That
    is the same shape as `feature_sig` reporting drift into the void for a week
    (2026-08-29) and `memory_guard` reporting to exit-0 stdout: a check whose output no
    one reads is not a check, it just looks like one on the way past.

⛔ THE GAP IS NOT HYPOTHETICAL. Two near-misses in two days, both of which this closes:
      * a test fixture - a deliberately broken `using` cycle - was committed into the
        source tree on 2026-08-30 during a background suite run. If a build had run in
        that window it would have packed two junk passives, and every source-reading
        gate would still have said clean, because by then the source was clean again.
      * the deploy step verifies only that a file with the right NAME exists in the Mods
        folder. A stale deploy - the game loading yesterday's pak while every tool
        reports on today's source - is invisible to every other check in the repo.

⚠ WHAT IT DOES NOT DO
    It does not open LSF or re-validate content; `validate.py` owns that on the source
    side. This asks one question the others cannot: **is what shipped what we wrote?**
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# ⭐ modconfig, not corpus_index. This tool only ever needed the mod's ROOT and NAME -
# pure layout, which modconfig provides - while corpus_index drags in the whole shipped
# stats corpus AND locates forge/ by a hardcoded parents[3], which is wrong for any mod
# not sitting exactly two levels under the repo. Dropping it is what let this file move
# into forge/ so EVERY scaffolded mod is gated, not just Warpblade (work-offline 103).
sys.path.insert(0, str(HERE))
import modconfig  # noqa: E402
import divine as _divine  # noqa: E402

# ⚠ load(), not find() - find() returns the PATH to forge.json, not the config. And
# start from the CWD rather than __file__: this tool now lives in forge/, so resolving
# relative to itself would look for the FRAMEWORK's config instead of the mod's.
# $FORGE_CONFIG still overrides, which is how build.ps1 points it at a workspace.
CFG = modconfig.load(modconfig.find())

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MOD = CFG.root
NAME = CFG.name
DIST = MOD / "dist" / f"{NAME}.pak"
DEPLOYED = Path(os.path.expandvars(
    r"%LOCALAPPDATA%\Larian Studios\Baldur's Gate 3\Mods")) / f"{NAME}.pak"

# What the build stages, expressed as a rule rather than a list, so this audit and
# build.ps1 cannot drift apart quietly. See build.ps1 step 4.
#   - Mods/ and Public/ wholesale
#   - Localization: compiled .loca ONLY (the .xml is the source, not the artifact)
#   - .png dropped from the stage: BG3 reads the .DDS, and the png is make_icons.py's
#     editable master. It used to ship - 245 KB, 22% of the payload - and was found by
#     the 2026-08-29 pak audit. If a png reappears in a pak, that regressed.
SHIP_DIRS = ("Mods", "Public")
NEVER_SHIP = (".png", ".xml", ".py", ".md", ".json")


def find_divine():
    # ⚠ PROBED. This used to take the first path that EXISTED and fall back to
    # shutil.which('divine.exe'), which returns VORTEX's divine - present, on
    # PATH, and unable to do the job. See forge/divine.py.
    return _divine.find_divine()


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def expected() -> dict[str, Path]:
    """{archive-relative path (posix, lowercased): source file} the pak SHOULD hold."""
    out: dict[str, Path] = {}
    for d in SHIP_DIRS:
        root = MOD / d
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if not f.is_file() or f.suffix.lower() in NEVER_SHIP:
                continue
            out[f.relative_to(MOD).as_posix().lower()] = f
    loc = MOD / "Localization"
    if loc.is_dir():
        for f in loc.rglob("*.loca"):
            out[f.relative_to(MOD).as_posix().lower()] = f
    return out


def extract(divine: Path, pak: Path, dest: Path) -> bool:
    r = subprocess.run([str(divine), "-g", "bg3", "-a", "extract-package",
                        "-s", str(pak), "-d", str(dest)],
                       capture_output=True, text=True, errors="replace", timeout=600)
    return r.returncode == 0


def audit_pak(divine: Path, pak: Path, label: str) -> list[dict]:
    out: list[dict] = []

    def add(sev, check, detail):
        out.append({"severity": sev, "pak": label, "check": check, "detail": detail})

    if not pak.is_file():
        add("ERROR", "missing", f"{pak} does not exist")
        return out

    tmp = Path(tempfile.mkdtemp(prefix="pak_audit_"))
    try:
        if not extract(divine, pak, tmp):
            # ⚠ UNREADABLE is UNKNOWN, not a mismatch. Reported as ERROR until
            # 2026-09-01, which conflated "the pak is wrong" with "I could not open
            # it" - two different verdicts with different consequences. Exit 2 is the
            # channel that already exists for "cannot read", and build.ps1 prints it
            # as UNKNOWN, not clean. Found when this gate started running for every
            # mod and hit a fixture whose Divine is a stub.
            add("UNKNOWN", "unreadable", f"Divine could not extract {pak}")
            return out

        got: dict[str, Path] = {}
        for f in tmp.rglob("*"):
            if f.is_file():
                got[f.relative_to(tmp).as_posix().lower()] = f

        want = expected()

        # ---- 1. everything the source says should ship, shipped ------------------
        for rel, src in sorted(want.items()):
            if rel not in got:
                add("ERROR", "missing-from-pak",
                    f"{rel} is in the source and NOT in the pak")

        # ---- 2. nothing extra -----------------------------------------------------
        for rel in sorted(got):
            if rel in want:
                continue
            suffix = Path(rel).suffix.lower()
            if suffix in NEVER_SHIP:
                add("ERROR", "should-never-ship",
                    f"{rel} is in the pak and its extension is on the never-ship list")
            else:
                add("WARN", "extra-in-pak",
                    f"{rel} is in the pak and has no source file behind it")

        # ---- 3. byte-identical ----------------------------------------------------
        differing = []
        for rel, src in sorted(want.items()):
            if rel not in got:
                continue
            if sha(src) != sha(got[rel]):
                differing.append(rel)
        for rel in differing:
            add("ERROR", "content-differs",
                f"{rel} in the pak is NOT byte-identical to its source")

        add("INFO", "counted",
            f"{len(want)} source file(s) expected, {len(got)} in the pak, "
            f"{len(want) - len(differing)} byte-identical")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Verify the built pak is what the source says it should be.")
    ap.add_argument("--deployed", action="store_true",
                    help="only the pak the game loads")
    # ⚠ The build needs these separately, and the reason is ordering: the pak is
    #   created at step 5 and deployed at step 6, so a mid-build audit of the DEPLOYED
    #   copy is comparing against the PREVIOUS build. The gate's first run reported
    #   exactly that - correctly, and uselessly. dist/ is checked before the deploy and
    #   the deployed copy after it, which is also the only way the deploy-stale check
    #   means anything.
    ap.add_argument("--dist", action="store_true",
                    help="only the freshly built pak in dist/")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    divine = find_divine()
    if not divine:
        print("Divine.exe not found - cannot read a pak. This is UNKNOWN, not clean.")
        return 2

    if a.deployed:
        targets = [(DEPLOYED, "deployed")]
    elif a.dist:
        targets = [(DIST, "dist")]
    else:
        targets = [(DIST, "dist"), (DEPLOYED, "deployed")]
    found: list[dict] = []
    for pak, label in targets:
        found += audit_pak(divine, pak, label)

    # ⭐ The deployed pak is the one the game opens. A build that wrote dist/ and failed
    #   to copy it is invisible to every other check in this repo - the source is fine,
    #   dist is fine, and the game is running last week's mod.
    if not a.deployed and not a.dist and DIST.is_file() and DEPLOYED.is_file():
        if sha(DIST) != sha(DEPLOYED):
            found.append({"severity": "ERROR", "pak": "both", "check": "deploy-stale",
                          "detail": "dist/ and the deployed pak are DIFFERENT files - "
                                    "the game is not running what was just built"})
        else:
            found.append({"severity": "INFO", "pak": "both", "check": "deploy-current",
                          "detail": "dist/ and the deployed pak are byte-identical"})

    if a.json:
        print(json.dumps(found, indent=2))
        if any(f["severity"] == "UNKNOWN" for f in found):
            return 2      # cannot read != mismatched; build.ps1 prints this as UNKNOWN
        return 1 if any(f["severity"] == "ERROR" for f in found) else 0

    errs = [f for f in found if f["severity"] == "ERROR"]
    warns = [f for f in found if f["severity"] == "WARN"]
    infos = [f for f in found if f["severity"] == "INFO"]
    unknown = [f for f in found if f["severity"] == "UNKNOWN"]
    for u in unknown:
        print("  UNKNOWN  %s - %s" % (u["check"], u["detail"]))
    print(f"pak audit - {NAME}\n")
    for f in errs + warns:
        print(f"{f['severity']:<6} [{f['pak']}] {f['check']}")
        print(f"       {f['detail']}")
    for f in infos:
        print(f"       [{f['pak']}] {f['detail']}")
    print(f"\n{len(errs)} error(s), {len(warns)} warning(s)")
    if not errs and not warns:
        print("the pak is what the source says it should be.")
    return 1 if errs else (2 if unknown else 0)


if __name__ == "__main__":
    raise SystemExit(main())
