# SPDX-License-Identifier: GPL-3.0-or-later
"""
Turn one source picture into the four class-icon DDS files, plus a preview.

WHY THIS EXISTS. `FORGE.md` PART 3.5 says, in as many words, "what you *should*
build is the processing" - and then nobody did, for four mods running. Every icon
so far was resized by hand, which is how a low-res pair goes missing: it looks
perfect on the machine that made it and blank for anyone playing on lower settings.

⭐ THE ART IS NOT THIS TOOL'S JOB, AND THAT IS DELIBERATE. Do not install a local
image generator. Write a good prompt, have the user run it through whatever they
already use, and hand the result here. A single pass through a real image tool has
beaten hours of local work every time it has been tried.

WHAT IT DOES
  - resizes one source image to the four sizes `forge.py` declares, so the numbers
    cannot drift away from the scaffolder's,
  - encodes DXT5 (BC3) DDS, which keeps the alpha the hotbar needs,
  - optionally strips a frame or a background, because the character-sheet icon and
    the hotbar icon are DIFFERENT PICTURES: the hotbar one has to read at 70-odd
    pixels, and a ring that looks handsome at 300px is mud at 72,
  - writes a side-by-side preview PNG at true scale, so the call can be made
    without launching the game.

⚠ IT WILL NOT INVENT THE HOTBAR VARIANT. If you pass one source it uses it for all
four and says so. Give `--hotbar-src` a separately cropped picture when you care.

MEASURED, NOT ASSERTED. Sizes come from `forge.py`'s `ICON_SIZES`. `FORGE.md`'s
prose says 150/70 and the scaffolder has emitted 152/72 since the first shipped
mod; this tool follows the code that shipped, and `icon_audit.py` checks against
the same single source.

USAGE
    py icon_build.py class.png --name Warpblade
    py icon_build.py class.png --hotbar-src hotbar.png --name Warpblade --strip-frame
    py icon_build.py class.png --name Warpblade --out ./icons --preview-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from PIL import Image
except ImportError as _e:                             # pragma: no cover
    # Say what actually failed. A hard-coded "install pillow" is a lying error
    # message the moment the real cause is anything else.
    print("icon_build cannot import Pillow: %s" % _e)
    print("if Pillow is installed, this is a shadowed module, not a missing one.")
    sys.exit(2)

try:
    import forge as _forge
    ICON_SIZES = list(_forge.ICON_SIZES)
except Exception:                                     # pragma: no cover
    ICON_SIZES = [("Assets/ClassIcons/{name}.DDS", 300),
                  ("Assets/ClassIcons/hotbar/{name}.DDS", 140),
                  ("AssetsLowRes/ClassIcons/{name}.DDS", 152),
                  ("AssetsLowRes/ClassIcons/hotbar/{name}.DDS", 72)]

BG_TOLERANCE = 24          # per-channel distance from a corner colour


def load_rgba(p: Path) -> Image.Image:
    im = Image.open(p)
    return im.convert("RGBA")


def drop_background(im: Image.Image, tol: int = BG_TOLERANCE) -> Image.Image:
    """Make pixels matching the four corners transparent.

    Deliberately dumb: it only touches colours the corners already agree on, so a
    picture with a real background survives and one on a flat plate loses it. A
    cleverer segmentation is exactly the 'rebuilding the glow' mistake PART 3.5
    warns about - minimal intervention wins.
    """
    px = im.load()
    w, h = im.size
    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    ref = corners[0]
    if any(abs(c[i] - ref[i]) > tol for c in corners for i in range(3)):
        return im                                     # corners disagree: leave it alone
    out = im.copy()
    op = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if abs(r - ref[0]) <= tol and abs(g - ref[1]) <= tol and abs(b - ref[2]) <= tol:
                op[x, y] = (r, g, b, 0)
    return out


def strip_frame(im: Image.Image) -> Image.Image:
    """Crop to the opaque content, then re-square it.

    A ring or plate that reads at 300px is mud at 72. Cropping to the glyph and
    letting it fill the tile is the whole trick.
    """
    bbox = im.getchannel("A").getbbox()
    if not bbox:
        return im
    im = im.crop(bbox)
    w, h = im.size
    side = max(w, h)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(im, ((side - w) // 2, (side - h) // 2))
    return out


def preview(pairs: list[tuple[str, Image.Image]], dest: Path) -> None:
    """One PNG, every size at TRUE scale, so 72px looks like 72px."""
    pad = 12
    width = sum(im.width for _, im in pairs) + pad * (len(pairs) + 1)
    height = max(im.height for _, im in pairs) + pad * 2
    sheet = Image.new("RGBA", (width, height), (32, 32, 36, 255))
    x = pad
    for _, im in pairs:
        sheet.alpha_composite(im, (x, pad))
        x += im.width + pad
    sheet.save(dest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="the character-sheet picture (PNG, ideally square)")
    ap.add_argument("--name", required=True, help="icon base name, e.g. Warpblade")
    ap.add_argument("--hotbar-src", help="a separately cropped picture for the hotbar sizes")
    ap.add_argument("--out", default="icons", help="output root (default ./icons)")
    ap.add_argument("--strip-frame", action="store_true",
                    help="crop to the opaque glyph before resizing")
    ap.add_argument("--drop-bg", action="store_true",
                    help="make a flat corner-coloured background transparent")
    ap.add_argument("--preview-only", action="store_true", help="write the preview, no DDS")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.is_file():
        print("icon_build: no such file: %s" % src)
        return 2

    base = load_rgba(src)
    hotbar = load_rgba(Path(args.hotbar_src)) if args.hotbar_src else base
    if not args.hotbar_src:
        print("note: no --hotbar-src, so the hotbar icons reuse the sheet picture.")
        print("      the hotbar one usually wants its frame and background gone.")

    for fn, flag in ((drop_background, args.drop_bg), (strip_frame, args.strip_frame)):
        if flag:
            base, hotbar = fn(base), fn(hotbar)

    out_root = Path(args.out)
    made: list[tuple[str, Image.Image]] = []
    for tmpl, size in ICON_SIZES:
        rel = tmpl.format(name=args.name)
        src_im = hotbar if "hotbar" in rel else base
        im = src_im.resize((size, size), Image.LANCZOS)
        made.append((rel, im))
        if args.preview_only:
            continue
        dest = out_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, format="DDS", pixel_format="DXT5")
        print("  %-46s %dx%d" % (rel, size, size))

    pv = out_root / ("%s_preview.png" % args.name)
    pv.parent.mkdir(parents=True, exist_ok=True)
    preview(made, pv)
    print()
    print("preview at true scale: %s" % pv)
    print("⚠ judge the 72px tile in that preview BEFORE packing - that is the one that fails.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
