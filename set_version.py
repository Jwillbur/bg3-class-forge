"""
Set the mod's version everywhere it is written, in one command.

BG3 packs a version into an int64:

    (major << 55) | (minor << 47) | (revision << 31) | build

and the value appears in four places that must agree. Two of them are inside
meta.lsx (ModuleInfo and PublishVersion - BG3MM reads the second), one is the
known-good modsettings template, and the fourth is the live modsettings.lsx the
game actually reads, which sits under %LOCALAPPDATA% and therefore needs the
explorer.exe shim to escape this app's MSIX write redirection. Doing that by
hand is four edits and one easy-to-forget shim, which is exactly the kind of
thing that drifts.

Bump policy, per the user's standing instruction (see CHANGELOG):
    build     ANY edit that reaches the pak but is too small to call a patch
    patch     a fix that changes nothing a player would describe differently
    minor     a new or reworked feature
    major     a rebuild that would invalidate an existing save

HARD RULE (user, 2026-08-27): if the pak is rebuilt, the version MOVES. There is
no such thing as an edit too small to number - that judgement is exactly what the
fourth field is for. "It was only a one-line fix" is how two installs end up
claiming the same version while behaving differently, which is unfalsifiable from
the outside and wastes a live test.

    py set_version.py --show          # what is set right now, everywhere
    py set_version.py --patch         # 1.4.0.0 -> 1.4.1.0
    py set_version.py --minor         # 1.4.1.0 -> 1.5.0.0
    py set_version.py --major         # 1.5.0.0 -> 2.0.0.0
    py set_version.py --set 1.4.2.0   # exact
    py set_version.py --patch --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from pathlib import Path
import sys

# --- mod config -----------------------------------------------------------
# Anchored on the MOD BEING AUDITED (the cwd), not on this file. Until
# 2026-09-06 every path below was the literal string CFG.name, so running
# this from another mod silently audited Warpblade instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402
CFG = modconfig.load(Path.cwd())


MOD = CFG.root
META = CFG.meta
TEMPLATE = MOD / "tools/modsettings.known-good.lsx"
MOD_UUID = "a05d576c-2536-42ef-920f-d1f621cc6434"


def encode(major: int, minor: int, revision: int, build: int) -> int:
    return (major << 55) | (minor << 47) | (revision << 31) | build


def decode(n: int) -> tuple[int, int, int, int]:
    return (n >> 55, (n >> 47) & 0xFF, (n >> 31) & 0xFFFF, n & 0x7FFFFFFF)


def fmt(n: int) -> str:
    return ".".join(str(x) for x in decode(n))


def current() -> int:
    """The authoritative value: ModuleInfo's, in meta.lsx."""
    text = META.read_text(encoding="utf-8-sig")
    # ModuleInfo's own Version64 - not PublishVersion's, and not a dependency's.
    block = text.split('<node id="ModuleInfo">', 1)
    if len(block) != 2:
        raise SystemExit("could not find ModuleInfo in meta.lsx")
    m = re.search(r'id="Version64"\s+type="int64"\s+value="(\d+)"', block[1])
    if not m:
        raise SystemExit("could not find ModuleInfo Version64 in meta.lsx")
    return int(m.group(1))


def live_modsettings() -> Path:
    return (Path(os.environ["LOCALAPPDATA"]) / "Larian Studios/Baldur's Gate 3"
            / "PlayerProfiles/Public/modsettings.lsx")


def version_near_uuid(text: str) -> int | None:
    """Our Version64 in a load-order file.

    modsettings lists every module, so the FIRST Version64 in the file belongs to
    whatever loads first - GustavX, normally. Scope to our own ModuleShortDesc by
    anchoring on the mod UUID, or --show reports someone else's version as ours.
    """
    i = text.find(MOD_UUID)
    if i == -1:
        return None
    m = re.search(r'id="Version64"\s+type="int64"\s+value="(\d+)"', text[i:i + 600])
    return int(m.group(1)) if m else None


def live_version() -> int | None:
    """Read the live file directly - safe, since only WRITES are redirected."""
    p = live_modsettings()
    if not p.is_file():
        return None
    return version_near_uuid(p.read_text(encoding="utf-8-sig", errors="replace"))


def publish_version() -> int | None:
    """meta.lsx's PublishVersion node - the one BG3MM reads, and a separate site
    from ModuleInfo. --sync used to skip it, so a half-applied bump could leave the
    manager advertising one version while the game loaded another."""
    text = META.read_text(encoding="utf-8-sig")
    pub = text.split('<node id="PublishVersion">', 1)
    if len(pub) != 2:
        return None
    m = re.search(r'value="(\d+)"', pub[1])
    return int(m.group(1)) if m else None


def rewrite(path: Path, old: int, new: int, expect: int) -> int:
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    s = raw.decode("utf-8-sig")
    n = s.count(str(old))
    if n != expect:
        raise SystemExit(f"{path.name}: expected {expect} occurrence(s) of {old}, found {n}. "
                         f"Refusing to write - fix the file or use --set.")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"")
                     + s.replace(str(old), str(new)).encode("utf-8"))
    return n


def rewrite_live(old: int, new: int) -> str:
    r"""Update the live modsettings from OUTSIDE the MSIX container.

    A direct write from here lands in this app's virtual store and the game never
    sees it, while reads merge the two and report success. So the write goes
    through a shim launched by explorer.exe, which is not a child of the packaged
    app, and the shim reports back to a non-redirected path.

    Everything the shim needs travels as an ENVIRONMENT VARIABLE rather than being
    interpolated into the command line. The first version of this built a
    single-quoted PowerShell string containing the path - and the path is
    "...\Baldur's Gate 3\...", whose apostrophe terminated the string and made the
    whole command a parse error. The write silently did not happen. Env vars have
    no quoting to get wrong.
    """
    target = live_modsettings()
    if not target.is_file():
        return "live modsettings.lsx not found - skipped"

    work = MOD / "dist"
    work.mkdir(parents=True, exist_ok=True)
    shim, report = work / "_setver.cmd", work / "_setver_report.txt"
    _quiet_unlink(report)

    ps = ('$p = $env:SETVER_PATH; '
          '$s = Get-Content -LiteralPath $p -Raw; '
          '$n = ([regex]::Matches($s, $env:SETVER_OLD)).Count; '
          'if ($n -ne 1) { "ABORT: found $n occurrence(s), expected 1"; exit 1 } '
          '$s = $s.Replace($env:SETVER_OLD, $env:SETVER_NEW); '
          'Set-Content -LiteralPath $p -Value $s -Encoding utf8; '
          '"OK: replaced $n"')
    lines = [
        "@echo off",
        f'set "SETVER_PATH={target}"',
        f'set "SETVER_OLD={old}"',
        f'set "SETVER_NEW={new}"',
        f'powershell -NoProfile -Command "{ps}" > "{report}" 2>&1',
    ]
    shim.write_text("\r\n".join(lines) + "\r\n", encoding="ascii", newline="")
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Start-Process explorer.exe -ArgumentList '\"{shim}\"'"],
                   capture_output=True)

    out = "TIMED OUT - the live modsettings.lsx was NOT updated"
    for _ in range(40):
        time.sleep(0.25)
        if report.is_file():
            out = report.read_text(encoding="utf-8", errors="replace").strip()
            break
    _quiet_unlink(shim)
    _quiet_unlink(report)

    # Never trust the shim's own word for it - re-read the file. This is the
    # whole reason the apostrophe bug was caught instead of shipped.
    actual = live_version()
    if actual == new:
        return f"v{fmt(new)} confirmed on disk"
    # out can be empty - the shim writes no report if it never got far enough to
    # produce one. splitlines()[0] then raised IndexError and took the whole
    # --set run down AFTER meta.lsx had already been rewritten, which reads as a
    # failed bump when the bump actually succeeded.
    said = out.splitlines()[0][:120] if out.strip() else "(nothing - shim produced no report)"
    return f"NOT UPDATED (file still reads {actual}). Shim said: {said}"


def _quiet_unlink(p: Path, tries: int = 12) -> None:
    """Delete, tolerating the handle still being open a moment after the write."""
    for _ in range(tries):
        try:
            p.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.25)





def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true")
    g.add_argument("--build", action="store_true")
    g.add_argument("--patch", action="store_true")
    g.add_argument("--minor", action="store_true")
    g.add_argument("--major", action="store_true")
    g.add_argument("--set", metavar="A.B.C.D")
    g.add_argument("--sync", action="store_true",
                   help="push meta.lsx's version out to any site that disagrees. "
                        "The repair path when a bump only half-applied")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    old = current()
    a, b, c, d = decode(old)

    if args.sync:
        # Each site is repaired against ITS OWN stale value, not a single `old`.
        # A half-applied bump leaves different files on different versions, which
        # is precisely the case a single old->new replace cannot fix.
        fixed = 0
        pv = publish_version()
        if pv is not None and pv != old:
            rewrite(META, pv, old, 1)
            print(f"  meta.lsx PublishVersion   v{fmt(pv)} -> v{fmt(old)}")
            fixed += 1
        if TEMPLATE.is_file():
            n = version_near_uuid(TEMPLATE.read_text(encoding="utf-8-sig"))
            if n is not None and n != old:
                rewrite(TEMPLATE, n, old, 1)
                print(f"  modsettings template     v{fmt(n)} -> v{fmt(old)}")
                fixed += 1
        lv = live_version()
        if lv is not None and lv != old:
            print(f"  live modsettings.lsx     {rewrite_live(lv, old)}")
            fixed += 1
        print(f"{fixed} site(s) synced to v{fmt(old)}" if fixed
              else f"all sites already agree on v{fmt(old)}")
        return 0

    if args.show:
        print(f"meta.lsx ModuleInfo      {old}  v{fmt(old)}")
        n = publish_version()
        if n is not None:
            flag = "" if n == old else "   <-- DISAGREES with ModuleInfo"
            print(f"meta.lsx PublishVersion  {n}  v{fmt(n)}{flag}")
        if TEMPLATE.is_file():
            n = version_near_uuid(TEMPLATE.read_text(encoding="utf-8-sig"))
            if n is None:
                print("modsettings template     (Warpblade not listed)")
            else:
                flag = "" if n == old else "   <-- DISAGREES"
                print(f"modsettings template     {n}  v{fmt(n)}{flag}")
        lv = live_version()
        if lv is None:
            print("live modsettings.lsx     (not found / mod not listed)")
        else:
            flag = "" if lv == old else "   <-- DISAGREES"
            print(f"live modsettings.lsx     {lv}  v{fmt(lv)}{flag}")
        return 0

    if args.set:
        parts = args.set.split(".")
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            raise SystemExit("--set needs four numbers, e.g. 1.4.2.0")
        new = encode(*(int(p) for p in parts))
    elif args.build:
        new = encode(a, b, c, d + 1)
    elif args.patch:
        new = encode(a, b, c + 1, 0)
    elif args.minor:
        new = encode(a, b + 1, 0, 0)
    else:
        new = encode(a + 1, 0, 0, 0)

    if new == old:
        print(f"already v{fmt(old)} - nothing to do")
        return 0

    print(f"v{fmt(old)}  ->  v{fmt(new)}")
    print(f"  {old}  ->  {new}")
    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    print(f"  meta.lsx                 {rewrite(META, old, new, 2)} site(s)")
    if TEMPLATE.is_file():
        print(f"  modsettings template     {rewrite(TEMPLATE, old, new, 1)} site(s)")
    print(f"  live modsettings.lsx     {rewrite_live(old, new)}")
    print("")
    print("The bump is only HALF done. Both of these, or it does not ship:")
    print(f"  1. add a '## v{fmt(new)}' heading to CHANGELOG.md")
    print("  2. rebuild so the pak carries it:  ./build.ps1")
    print("")
    print("release_check.py reads that CHANGELOG heading as the shipped version.")
    print("There is no DESIGN_VERSION constant any more, so nothing else records it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
