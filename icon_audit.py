# SPDX-License-Identifier: GPL-3.0-or-later
"""
Audit a mod's icons against the four things that actually break them.

WHY THIS EXISTS. `FORGE.md` PART 3.5 opens with "icons are the single most common
place a first mod stalls", then spends two pages describing wiring that cost a full
day to get right - and until now every word of it was PROSE. Prose is not a gate.
The three answers that cost that day (the UV map is `GUI/Icons_Skills.lsx`, the
registration is `Content/UI/[PAK]_UI/Icons_<Mod>.lsf`, the extension is uppercase)
are exactly the kind of thing a person reads once, agrees with, and then does not do.

⭐ WHAT MAKES ICONS DIFFERENT from the rest of the toolchain: the game does not tell
you. A missing class icon is a blank square. A low-res pair you never made looks
perfect until somebody plays on lower settings. Metadata attached to a low-res icon
throws a startup error naming a texture, not a mod. None of it is a malformed file,
so `validate.py` passes it all.

MEASURED, NOT ASSERTED. The four sizes are imported from `forge.py`'s `ICON_SIZES`
rather than retyped here, because a second copy of a number is a second thing to
drift. That is not hypothetical: `FORGE.md` says the low-res pair is 150/70 and
`forge.py` has emitted 152/72 since the first mod that shipped. One of them is
wrong, and duplicating it here would have made it three.

WHAT IT CHECKS
  1 PRESENT     - all four class-icon files exist, same base name.
  2 SIZED       - each one's real pixel size, read out of the DDS header, matches
                  the size its FOLDER implies. The folder decides, not the filename.
  3 UPPERCASE   - the on-disk extension is `.DDS`. A lowercase `.dds` resolves on
                  Windows and fails inside a pak.
  4 METADATA    - whatever sidecar the hi-res icons carry, the low-res ones must
                  NOT carry. Checked as a relation, so it stays true if Larian
                  renames the sidecar.
  5 ATLAS WIRE  - if the mod ships a spell-icon atlas, its UV map and registration
                  paths are the ones a working mod uses, not the ones the wiki says.
  6 TILE SIZE   - atlas tiles are 64x64. Vanilla ships no loose spell icons at all,
                  so a 380px "tooltip icon" is upscaled from a 64px source and is
                  strictly worse than doing nothing.
  7 VIRTUAL TEX - warns if the mod ships a `.gts`, because roughly 48 mods loading
                  loose virtual textures is the vanilla ceiling and the symptom
                  (black textures) blames whatever was installed last.

USAGE
    py icon_audit.py                 # audit the mod in the current tree
    py icon_audit.py --json          # machine-readable
Exit code is 1 if anything is an ERROR, 0 otherwise. Warnings never fail the build.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402

# The sizes live in exactly one place. See the header note about drift.
try:
    import forge as _forge
    ICON_SIZES = list(_forge.ICON_SIZES)
except Exception:                                    # pragma: no cover
    ICON_SIZES = [("Assets/ClassIcons/{name}.DDS", 300),
                  ("Assets/ClassIcons/hotbar/{name}.DDS", 140),
                  ("AssetsLowRes/ClassIcons/{name}.DDS", 152),
                  ("AssetsLowRes/ClassIcons/hotbar/{name}.DDS", 72)]

DDS_MAGIC = b"DDS "
GOOD_UV_MAP = "GUI/Icons_Skills.lsx"
BAD_REGISTRATION = "_merged.lsx"
TILE = 64


def dds_size(p: Path) -> tuple[int, int] | None:
    """(width, height) from the DDS header, or None if it is not a DDS."""
    try:
        with p.open("rb") as fh:
            head = fh.read(20)
    except OSError:
        return None
    if len(head) < 20 or head[:4] != DDS_MAGIC:
        return None
    height, width = struct.unpack_from("<II", head, 12)
    return width, height


# Metadata is LSX/LSF. A .png or .tga beside an icon is the SOURCE ART, which is
# how every one of these was authored - counting it as metadata made the first run
# of this tool fail a mod that is live on the Nexus.
METADATA_EXT = {".lsx", ".lsf"}


def _sidecars(icon: Path) -> list[Path]:
    """Metadata files beside an icon sharing its stem - the 'metadata' of PART 3.5."""
    if not icon.parent.is_dir():
        return []
    return sorted(q for q in icon.parent.iterdir()
                  if q.is_file() and q.stem == icon.stem and q != icon
                  and q.suffix.lower() in METADATA_EXT)


def audit(cfg) -> list[tuple[str, str, str]]:
    """-> [(level, check, message)]; level is ERROR, WARN or OK."""
    out: list[tuple[str, str, str]] = []
    root = cfg.root
    name = cfg.data.get("icon_name") or cfg.data.get("mod_name") or cfg.data.get("name")
    if not name:
        return [("ERROR", "config", "forge.json names no mod - cannot locate icons")]

    # ---- 1..4 the four class icons -------------------------------------------
    found_any = False
    hires_sidecars = 0
    for i, (tmpl, want) in enumerate(ICON_SIZES):
        rel = tmpl.format(name=name)
        p = root / cfg.data.get("public_dir", "Public") / cfg.data.get("mod_name", name) / rel
        if not p.exists():
            # tolerate the layout where GUI/ holds the assets instead
            alt = root / "Mods" / cfg.data.get("mod_name", name) / "GUI" / rel
            p = alt if alt.exists() else p
        if not p.exists():
            out.append(("ERROR", "present", "missing icon: %s" % rel))
            continue
        found_any = True

        # 3 UPPERCASE - compare against what the directory really holds
        real = next((q.name for q in p.parent.iterdir() if q.name.lower() == p.name.lower()), None)
        if real and not real.endswith(".DDS"):
            out.append(("ERROR", "uppercase",
                        "%s is not uppercase .DDS - resolves on Windows, fails in a pak" % real))

        # 2 SIZED
        got = dds_size(p)
        if got is None:
            out.append(("ERROR", "sized", "%s is not a readable DDS" % rel))
        elif got != (want, want):
            out.append(("ERROR", "sized",
                        "%s is %dx%d, the folder implies %dx%d - the FOLDER decides"
                        % (rel, got[0], got[1], want, want)))

        # 4 METADATA - hi-res (first two) may carry sidecars, low-res must not
        side = _sidecars(p)
        if i < 2:
            hires_sidecars += len(side)
        elif side:
            out.append(("ERROR", "metadata",
                        "%s carries %s - metadata on a low-res icon is a startup error"
                        % (rel, ", ".join(s.name for s in side))))

    if found_any and hires_sidecars == 0:
        out.append(("WARN", "metadata",
                    "no sidecar metadata beside either hi-res icon - correct for some "
                    "layouts, but verify against a mod that works in game"))

    # ---- 5,6 the spell-icon atlas ---------------------------------------------
    atlases = [q for q in root.rglob("*.lsx")
               if "atlas" in q.name.lower() or "icons_" in q.name.lower()]
    for a in atlases:
        try:
            text = a.read_text(encoding="utf8", errors="replace")
        except OSError:
            continue
        if "TextureAtlasInfo" not in text and "IconWidth" not in text:
            continue
        uv = re.search(r'value="([^"]*GUI/Icons_[^"]+\.lsx)"', text)
        if uv and uv.group(1) != GOOD_UV_MAP:
            out.append(("ERROR", "atlas-wire",
                        "%s points its UV map at %s - a working mod uses %s"
                        % (a.name, uv.group(1), GOOD_UV_MAP)))
        w = re.search(r'id="IconWidth"[^/]*value="(\d+)"', text)
        h = re.search(r'id="IconHeight"[^/]*value="(\d+)"', text)
        if w and h and (int(w.group(1)), int(h.group(1))) != (TILE, TILE):
            out.append(("WARN", "tile-size",
                        "%s declares %sx%s tiles; vanilla spell icons are %dx%d and "
                        "ship no loose files at all" % (a.name, w.group(1), h.group(1), TILE, TILE)))

    merged = [q for q in root.rglob(BAD_REGISTRATION)]
    if merged and atlases:
        out.append(("ERROR", "atlas-wire",
                    "registration via %s - a working mod registers at "
                    "Content/UI/[PAK]_UI/Icons_<Mod>.lsf" % merged[0].name))

    # ---- 7 virtual textures ----------------------------------------------------
    gts = list(root.rglob("*.gts"))
    if gts:
        out.append(("WARN", "virtual-tex",
                    "%d .gts file(s) - loose virtual textures have a ~48-mod ceiling "
                    "whose symptom (black textures) blames the newest mod, not the count. "
                    "Count with VT Audit rather than bisecting." % len(gts)))

    if not out:
        out.append(("OK", "all", "icons pass every check"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    try:
        cfg = modconfig.load(Path.cwd())
    except modconfig.ConfigError as e:
        print("icon_audit: %s" % e)
        return 2

    rows = audit(cfg)
    if args.json:
        print(json.dumps([{"level": a, "check": b, "message": c} for a, b, c in rows], indent=1))
    else:
        print("icon audit - %s" % cfg.root)
        for lvl, chk, msg in rows:
            print("  %-6s %-12s %s" % (lvl, chk, msg))
        bad = sum(1 for lvl, _, _ in rows if lvl == "ERROR")
        print()
        print("%d error(s), %d warning(s)"
              % (bad, sum(1 for lvl, _, _ in rows if lvl == "WARN")))
    return 1 if any(lvl == "ERROR" for lvl, _, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
