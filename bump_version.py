# SPDX-License-Identifier: GPL-3.0-or-later
"""Move a mod's version, in every place BG3 records it, for any forge mod.

    py bump_version.py --show          # what is set right now, everywhere
    py bump_version.py --build         # 1.8.1.0 -> 1.8.1.1  (and 1.8.1.9 -> 1.8.2.0)
    py bump_version.py --patch         # 1.8.1.1 -> 1.8.2.0
    py bump_version.py --minor         # 1.8.2.0 -> 1.9.0.0
    py bump_version.py --major         # 1.9.0.0 -> 2.0.0.0
    py bump_version.py --set 1.8.2.0   # exact
    py bump_version.py --sync          # repair a half-applied bump
    py bump_version.py --check         # exit 1 if source moved and the version did not

Paths come from forge.json via modconfig, so this works on any mod the forge
builds. Point it at another with FORGE_CONFIG, same as every other tool here.

BG3 packs the version into an int64:

    (major << 55) | (minor << 47) | (revision << 31) | build

⭐ HARD RULE (user, 2026-08-27): IF THE PAK IS REBUILT, THE VERSION MOVES.

There is no edit too small to number. An edit judged too small to call a patch
takes the FOURTH field -- the one almost nobody uses -- via `--build`.

The reason this is a rule and not a habit is that the failure is INVISIBLE. Two
paks claiming the same version while behaving differently cannot be told apart
from the outside: not by a mod manager, not by an update check, not by the person
testing, and not by the person who built them. So a live test run against the
wrong pak produces exactly the same evidence as a fix that did not work, and the
next hour is spent re-diagnosing something that was already fixed. Numbering
costs one command; not numbering costs a test session and trusts memory for the
rest of the mod's life.

⚠ WHY --check EXISTS AND WHY IT LOOKS AT UNCOMMITTED WORK.
Warpblade's equivalent check only ever compared COMMITS against the last version
bump. That leaves the entire window between editing a file and committing it
unguarded -- which is precisely the window in which a pak gets rebuilt and handed
to a tester. Missed for real on 2026-08-27: a fix was edited, built, deployed and
reported while the version sat still, and the check reported clean throughout.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402

# A console inherits whatever codepage it has (cp1252 in Git Bash here) and this
# module's docstring is UTF-8. Without this, `--help` dies on the first star -
# argparse prints the docstring as its description. Measured, not hypothetical.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------- packing ----

def roll(major: int, minor: int, revision: int, build: int) -> tuple:
    """Carry each field at 10, the way an odometer does.

    ⭐ THE USER'S SCHEME, SET 2026-09-03, AND IT IS NOT SEMVER. Every field is a single
    digit: 1.1.0.9 + a hotfix is 1.1.1.0, not 1.1.0.10. A field never shows double
    digits, and the leading number only ever moves because the one after it rolled.

        4th  hotfixes
        3rd  small patches
        2nd  major changes
        1st  only when the second rolls over

    Without this, `--build` nine times in a day produced 1.1.0.10 - a version that sorts
    below 1.1.0.9 in any human reading of it, and that the packing format is happy to
    encode, so nothing would have complained.
    """
    if build > 9:
        build, revision = 0, revision + 1
    if revision > 9:
        revision, minor = 0, minor + 1
    if minor > 9:
        minor, major = 0, major + 1
    return major, minor, revision, build


def encode(major: int, minor: int, revision: int, build: int) -> int:
    return (major << 55) | (minor << 47) | (revision << 31) | build


def decode(n: int) -> tuple[int, int, int, int]:
    return (n >> 55, (n >> 47) & 0xFF, (n >> 31) & 0xFFFF, n & 0x7FFFFFFF)


def fmt(n: int) -> str:
    return "%d.%d.%d.%d" % decode(n)


# ------------------------------------------------------------------ sites ----
# meta.lsx holds the version TWICE, in two nodes that serve different readers:
# ModuleInfo is what the game loads, PublishVersion is what BG3MM displays. They
# drift independently, and a bump that repairs only one leaves the manager
# advertising a version the game is not running. Both are read separately here
# for that reason, never as "the number in meta.lsx".

def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def module_version(meta: Path) -> int | None:
    """ModuleInfo's own Version64 -- not PublishVersion's, and not a dependency's."""
    t = _text(meta)
    head = t.split('<node id="PublishVersion">', 1)[0]
    mods = head.split('<node id="ModuleInfo">', 1)
    if len(mods) != 2:
        return None
    m = re.search(r'id="Version64"[^>]*value="(\d+)"', mods[1])
    return int(m.group(1)) if m else None


def publish_version(meta: Path) -> int | None:
    t = _text(meta)
    tail = t.split('<node id="PublishVersion">', 1)
    if len(tail) != 2:
        return None
    m = re.search(r'value="(\d+)"', tail[1])
    return int(m.group(1)) if m else None


def rewrite(path: Path, old: int, new: int, expect: int) -> int:
    """Replace one exact number. REFUSES on a surprising count rather than guessing.

    A blind replace here would be happy to rewrite a dependency's Version64 that
    happens to share the value, which is a corruption nothing downstream reports.
    """
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    s = raw.decode("utf-8-sig")
    n = s.count(str(old))
    if n != expect:
        raise SystemExit(
            "%s: expected %d occurrence(s) of %d, found %d. Refusing to write - "
            "fix the file or use --set." % (path.name, expect, old, n))
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"")
                     + s.replace(str(old), str(new)).encode("utf-8"))
    return n


# ------------------------------------------------------------------ check ----

def _git(root: Path, *a: str) -> str:
    r = subprocess.run(["git", *a], cwd=str(root), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else ""


def check(cfg) -> list[str]:
    """Report why the version is stale, or nothing at all. Never writes."""
    root, out = cfg.root, []
    if not _git(root, "rev-parse", "--git-dir"):
        return ["not a git repository - --check has nothing to compare against"]

    try:
        meta_rel = cfg.meta.relative_to(root).as_posix()
    except ValueError:
        return ["meta.lsx is outside the repo root - cannot compare"]

    watched = [p for p in ("Public", "Localization", "Mods") if (root / p).is_dir()]
    pending_meta = _git(root, "diff", "HEAD", "--", meta_rel)
    version_moving = "Version64" in pending_meta

    # 1. uncommitted source. The window a pak actually ships in.
    src = _git(root, "diff", "HEAD", "--stat", "--", *watched)
    files = [x for x in src.splitlines() if "|" in x
             and "meta.lsx" not in x]
    if files and not version_moving:
        out.append(
            "VERSION NOT BUMPED - %d uncommitted source file(s) differ from HEAD "
            "while meta.lsx's Version64 does not. Anything that reaches the pak "
            "moves the version: --patch, or --build if it is too small to call a "
            "patch." % len(files))

    # 2. commits since the version last moved. -G matches the diff TEXT; -S counts
    #    occurrences of a string, and editing a value does not change how many
    #    times "Version64" appears, so -S matches the commit that ADDED the line.
    bumped = _git(root, "log", "-1", "--format=%H",
                  "-G", r'Version64.*value="[0-9]+"', "--", meta_rel)
    if bumped and not version_moving:
        after = [x for x in _git(root, "log", "--oneline", "%s..HEAD" % bumped,
                                 "--", *watched).splitlines() if x.strip()]
        if after:
            out.append(
                "VERSION IS STALE - %d commit(s) changed the mod's source since the "
                "version last moved (oldest: %s)." % (len(after), after[-1][:60]))
    return out


# ------------------------------------------------------------------- main ----

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true")
    g.add_argument("--check", action="store_true",
                   help="exit 1 if source moved and the version did not")
    g.add_argument("--build", action="store_true",
                   help="the fourth field - for an edit too small to call a patch")
    g.add_argument("--patch", action="store_true")
    g.add_argument("--minor", action="store_true")
    g.add_argument("--major", action="store_true")
    g.add_argument("--set", metavar="A.B.C.D")
    g.add_argument("--sync", action="store_true",
                   help="push ModuleInfo's version out to any site that disagrees")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        # cwd, NOT Path(__file__). This tool lives in forge/, which is shared and
        # sits beside no mod at all - resolving from its own location would search
        # upward from the framework and find nothing. Run it from the mod root, or
        # point FORGE_CONFIG at one.
        cfg = modconfig.load()
    except modconfig.ConfigError as e:
        print("cannot locate a mod: %s" % e, file=sys.stderr)
        return 2

    meta = cfg.meta
    if not meta.is_file():
        print("no meta.lsx at %s" % meta, file=sys.stderr)
        return 2

    old = module_version(meta)
    if old is None:
        print("meta.lsx has no ModuleInfo Version64", file=sys.stderr)
        return 2
    a, b, c, d = decode(old)
    pub = publish_version(meta)

    if args.check:
        problems = check(cfg)
        for p in problems:
            print("ERROR  %s" % p)
        if not problems:
            print("v%s - version is in step with the source." % fmt(old))
        return 1 if problems else 0

    if args.show:
        print("meta.lsx ModuleInfo      %d  v%s" % (old, fmt(old)))
        if pub is not None:
            flag = "" if pub == old else "   <-- DISAGREES with ModuleInfo"
            print("meta.lsx PublishVersion  %d  v%s%s" % (pub, fmt(pub), flag))
        return 0

    if args.sync:
        if pub is not None and pub != old:
            if args.dry_run:
                print("  PublishVersion  v%s -> v%s  (--dry-run)" % (fmt(pub), fmt(old)))
            else:
                rewrite(meta, pub, old, 1)
                print("  PublishVersion  v%s -> v%s" % (fmt(pub), fmt(old)))
            return 0
        print("all sites already agree at v%s" % fmt(old))
        return 0

    if args.set:
        parts = args.set.split(".")
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            raise SystemExit("--set needs four numbers, e.g. 1.8.2.0")
        new = encode(*(int(p) for p in parts))
    elif args.build:
        new = encode(*roll(a, b, c, d + 1))
    elif args.patch:
        new = encode(*roll(a, b, c + 1, 0))
    elif args.minor:
        new = encode(*roll(a, b + 1, 0, 0))
    else:
        new = encode(a + 1, 0, 0, 0)

    if new == old:
        print("already v%s - nothing to do" % fmt(old))
        return 0
    if new < old:
        print("refusing to move v%s BACKWARDS to v%s. Use --set if you mean it."
              % (fmt(old), fmt(new)), file=sys.stderr)
        return 2

    print("v%s  ->  v%s" % (fmt(old), fmt(new)))
    if args.dry_run:
        print("--dry-run: nothing written")
        return 0

    # Both nodes only share a value when they already agreed. When they did not,
    # repair PublishVersion off its OWN stale number first - a single old->new
    # replace is exactly what cannot fix a half-applied bump.
    if pub is not None and pub != old:
        rewrite(meta, pub, new, 1)
        print("  PublishVersion was v%s - repaired to v%s" % (fmt(pub), fmt(new)))
        rewrite(meta, old, new, 1)
    else:
        rewrite(meta, old, new, 2 if pub is not None else 1)
    print("  meta.lsx updated")
    print("")
    print("The bump is only HALF done. Both of these, or it does not ship:")
    print("  1. add a '## v%s' heading to CHANGELOG.md" % fmt(new))
    print("  2. rebuild so the pak carries it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
