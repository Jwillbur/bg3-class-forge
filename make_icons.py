#!/usr/bin/env python3
"""
make_icons.py - turn three source artworks into the complete BG3 icon set.

    art/class icon.png  -> Warpblade in ClassIcons/        (character sheet)
    art/hotbar icon.png -> Warpblade in ClassIcons/hotbar/ (hotbar ring, level-up)
    art/resource.png    -> WarpDie                         (the Warp Dice resource)

The two ClassIcons folders are independent surfaces and the game ships different
art in each. The hotbar one is a filled medallion built to survive 70px inside a
ring; the character-sheet one is the open emblem. See the note above CLASS_TARGETS.

WHAT THIS DOES, AND WHY EACH STEP EXISTS
----------------------------------------
1. CUT OUT THE BACKGROUND. Both sources are AI art on an opaque backdrop (the
   class emblem on black-with-a-glow, the die on a grey vignette). BG3 icons are
   composited over the UI, so an opaque backdrop shows up as an ugly card behind
   the art. Every shipped icon checked is 32-bit ARGB.

   Method: edge-barrier region growing. Backgrounds here are SMOOTH (low colour
   gradient); the art has hard, high-contrast edges. So: compute gradient
   magnitude, mark low-gradient pixels as passable, then flood inward from the
   image border. The flood spreads through the smooth backdrop and stops dead at
   the art silhouette. Dark areas INSIDE the art are never reached, so they stay
   opaque - which a simple "black is transparent" threshold would have destroyed.

2. DE-FRINGE. Partially-transparent edge pixels still carry the old backdrop
   colour, which reads as a dark halo. The mask is eroded ~1px before feathering
   so the soft edge is cut from art-coloured pixels instead.

3. LARIAN-STYLE FINISH. Their class icons read at 70px on a dark panel: strong
   silhouette, punchy contrast, and a soft light behind the subject separating it
   from the background. So: mild contrast + saturation lift, then a coloured rim
   glow composited BEHIND the art.

4. EXPORT EVERY REQUIRED SIZE AND STATE, then convert to DDS and regenerate
   metadata.lsf. Sizes and paths were taken from a shipped, working mod
   (Expansion.pak), not guessed - see the tables below.

NOTE ON PNG vs DDS: a shipped mod's Assets/ClassIcons PNG is 512x512 while its
metadata declares 300x300 - because the PNG is just a leftover source artifact.
The DDS is what the game loads, and metadata w/h must match the DDS. We still
ship the PNG because MapKey references the .png path.

Usage:  py tools/make_icons.py [--no-glow]
"""

import argparse
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageEnhance

# --- mod config -----------------------------------------------------------
# Anchored on the MOD BEING AUDITED (the cwd), not on this file. Until
# 2026-09-06 every path below was the literal string CFG.name, so running
# this from another mod silently audited Warpblade instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402
CFG = modconfig.load(Path.cwd())


# ------------------------------------------------------------------ config --
HERE = Path(__file__).resolve().parent
# ⛔ MODROOT WAS `HERE.parent`, WHICH IS THE TOOLKIT ROOT, NOT THE MOD.
# This script lives in forge/, so HERE.parent is always C:/Modding/Toolkit no matter
# which mod is being built. Every source-art and output path below hung off it, so
# running it from a second mod looked for that mod's artwork in the Toolkit root and
# reported "source art missing" with a path the user never chose.
#
# The comment above records the 2026-09-06 half of this same bug: the paths were the
# literal string "Warpblade" and were changed to CFG.name. The NAME was fixed and the
# ROOT was left hardcoded, so the tool still only worked for one mod.
#
# CFG.root is the directory containing forge.json - the same answer every other forge
# tool uses. Found 2026-09-11 building the second mod in this repo; same bug class as
# memory_audit.py's single-mod version check, fixed the same day.
MODROOT = CFG.root
GUI = MODROOT / "Mods" / CFG.name / "GUI"
OBJ = MODROOT / "obj" / "icons"

TEXCONV = Path(r"C:\Modding\tools\texconv\texconv.exe")
DIVINE = Path(r"C:\Modding\tools\lslib\Packed\Tools\Divine.exe")

# Source art lives IN THE REPO. These used to point at the user's Downloads
# folder, which meant the only copy of the artwork sat outside version control,
# one folder cleanup away from being gone. art/ is tracked.
#
# TWO class artworks, and the split is round-vs-square rather than
# hotbar-vs-character-sheet, because the LEVEL-UP subclass-selection screen at
# Fighter 3 reads the HOTBAR slot. Confirmed in game, and not a fallback:
# Game.pak has exactly four ClassIcons folders and this mod ships all four. So
# whatever sits in `hotbar/` is what a player sees at the moment they choose the
# subclass, and it has to work at 70px inside a ring.
#
# art/hotbar icon.png is now DERIVED from art/class icon.png by tools/strip_ring.py
# rather than drawn separately: same emblem, brass ring and purple field removed, so
# the two surfaces cannot drift apart when the artwork is replaced. That is also what
# Larian does - see the Game.pak table below, where the hotbar pair is the ringed
# emblem with its ring stripped.
#
# SUPERSEDED, kept because the reasoning still applies to any future hotbar art: the
# 2026-08-20 hotbar source was the class emblem re-cut as a filled medallion, a dark
# core behind a bright silhouette, filling the circle edge to edge. Measured at
# the size the game actually draws it, the open version loses - its tendrils and
# thin blade dissolve into violet mush at 70px, while the medallion still reads
# as a sword. That is also why vanilla's own hotbar icons are heavy shapes.
#
# Two deliberate departures from vanilla in that file, both chosen with the
# numbers in hand rather than by accident:
#   * it fills the ring, where vanilla art stops at 0.83 of the half-canvas;
#   * the blade tip runs off the bottom rim. 723 of its 909 boundary pixels sit
#     in the 225-315 degree band. Uniform scaling cannot fix it - blade and disc
#     edge both sit at radius 1.000, so they shrink together - it would need the
#     sword moved up inside the frame. Accepted as a medallion design.
CLASS_SRC = MODROOT / "art" / "class icon.png"
HOTBAR_SRC = MODROOT / "art" / "hotbar icon.png"
RES_SRC = MODROOT / "art" / "resource.png"

# ⛔ NOT EVERY CLASS HAS A SPENDABLE RESOURCE, and this script used to require one.
# It demanded art/resource.png unconditionally and exited before drawing anything, so a
# subclass whose design forbids new resources could not generate its CLASS icon either.
# Found 2026-09-11 on The Uncrowned God, whose bible bans new resources outright: the
# corpse it consumes is the resource.
#
# The test is the ARTIFACT, not the config. A forge.json can still carry a leftover
# resource_name from scaffolding; what decides whether the game has a resource is
# whether the mod actually ships an ActionResourceDefinitions directory.
HAS_RESOURCE = (MODROOT / "Public" / CFG.name / "ActionResourceDefinitions").is_dir()

CLASS_NAME = CFG.name
RES_NAME = "WarpDie"

ACCENT = (78, 168, 255)  # the arcane blue the WarpDie artwork uses

# Larian's class-icon brass band, measured from Game.pak: every dominant palette
# entry across the Fighter family sits in hue 19-35. Widened slightly for the
# highlights and shadows either side, with a soft falloff beyond it.
BRASS_LO, BRASS_HI, BRASS_FALLOFF = 15.0, 50.0, 35.0

# ---------------------------------------------------------------------------
# ICON SPEC, MEASURED FROM Game.pak ON 2026-08-19 - not from Expansion.pak.
#
# The old tables came from a shipped MOD's metadata, and they were wrong in four
# ways that all showed up in play:
#   * LowRes class icons were 150/70. Vanilla is 152/72.
#   * The resource icon shipped ONLY under ActionResources_c/Icons/Resources.
#     Vanilla also ships it one level UP at ActionResources_c/Icons - a different,
#     larger icon - and that is the one the character sheet, tooltips and the
#     level-up screen read. Missing it is why those three showed a generic starry
#     die instead of ours.
#   * ActionResources_c/Icons/Resources is 44x64, NOT square. We shipped 48x48.
#   * CC/icons_resources is 128x128 and full-bleed. We shipped a 48x48 die.
#
# FRAMING also comes from measurement. Vanilla class icons do NOT fill their
# canvas and are NOT vertically centred: across BattleMaster and Champion, in all
# four sizes, the art occupies ~66-71% of the width and ~74-81% of the height and
# sits ABOVE centre - about -5% of canvas height on the character-sheet icons and
# about -2.75% on the hotbar ones. Ours filled 82-100% of the height, dead centre,
# touching the top and bottom edges. That is the reported "off-centre on the
# hotbar": against vanilla neighbours it read as too big and sitting too low.
# ---------------------------------------------------------------------------

# (relative dir under GUI, w, h, register in metadata?, content height frac,
#  y offset frac, master: "class" or "small", CIRCLE-FIT radius or None)
#
# The two hotbar rows are circle-fitted rather than height-fitted. Everything in
# that slot is drawn inside a round frame - the hotbar ring, and the level-up
# subclass buttons - so what matters is the art's furthest pixel FROM THE CENTRE,
# not its height. Measured on vanilla's own hotbar icons, max radius as a
# fraction of half-canvas: BattleMaster 0.836, Champion 0.802, EldritchKnight
# 0.851, bbox centres within 3px of the canvas centre.
#
# BACK TO VANILLA'S 0.83 ON 2026-08-21, from the 1.00 the medallion needed. That
# 1.00 was correct for what it was fitting: a FILLED medallion, brass rim and all,
# which is meant to reach the edge - 0.83 would have floated it in the hotbar ring
# with a dead gap. The current hotbar art is not that. It is the class emblem with
# the ring and field stripped out (tools/strip_ring.py), which is structurally the
# same thing Larian ships - Game.pak's own hotbar icons are the ringed emblem with
# its ring removed - so it wants Larian's framing, not the medallion's. At 1.00 the
# crown tip and blade end would touch the hotbar ring's inner edge; 0.83 is the
# clearance vanilla leaves, and all three vanilla samples agree to within 0.05.
CLASS_TARGETS = [
    ("Assets/ClassIcons",              300, 300, True,  0.79, -0.060, "class", None),
    ("Assets/ClassIcons/hotbar",       140, 140, True,  0.79,  0.000, "hotbar", 0.83),
    ("AssetsLowRes/ClassIcons",        152, 152, False, 0.79, -0.060, "class", None),
    ("AssetsLowRes/ClassIcons/hotbar",  72,  72, False, 0.79,  0.000, "hotbar", 0.83),
]
CLASS_MAX_W = 0.72     # vanilla never exceeds ~71% of canvas width
#
# THE SPLIT IS BY FOLDER, NOT BY SIZE. This is the correction to the note that
# used to sit here, and getting it backwards is what broke the icons in game on
# 2026-08-19.
#
# The old note claimed the `hotbar` folder "is not hotbar-only - BG3 reads it for
# the level-up screen and the character sheet too". That was the wrong conclusion
# drawn from a real symptom. The actual cause was in this script: the source used
# to be picked by SIZE (`art_small if cw <= 152 else art`), and 152 is
# AssetsLowRes/ClassIcons - THE CHARACTER SHEET at low-res assets. So a simplified
# variant intended for the hotbar was also written to the character-sheet slot,
# and the leak was ours, not the game's.
#
# Ground truth, extracted from Game.pak 2026-08-20 (BattleMaster):
#   Assets/ClassIcons/          300  ringed emblem
#   AssetsLowRes/ClassIcons/    152  ringed emblem   <- SAME artwork as the 300
#   Assets/ClassIcons/hotbar/   140  ring stripped
#   AssetsLowRes/ClassIcons/hotbar/ 72  ring stripped <- SAME artwork as the 140
#
# So the two folders are genuinely independent surfaces and Larian ships different
# art in each. Selection is now by the explicit source field above.

# (relative dir, w, h, content height frac, max width frac, states?, register?)
# Every size and content fraction below is the measured vanilla SuperiorityDie.
RES_TARGETS = [
    ("Assets/ActionResources_c/Icons",              80,  80, 0.84, 0.75, True,  True),
    ("Assets/ActionResources_c/Icons/Resources",    44,  64, 0.69, 0.78, True,  True),
    ("Assets/Shared/Resources",                     48,  48, 0.88, 0.83, True,  True),
    ("Assets/CC/icons_resources",                  128, 128, 1.00, 1.00, False, True),
    ("AssetsLowRes/ActionResources_c/Icons",        40,  40, 0.85, 0.75, True,  False),
    ("AssetsLowRes/ActionResources_c/Icons/Resources", 24, 32, 0.69, 0.78, False, False),
    ("AssetsLowRes/Shared/Resources",               24,  24, 0.88, 0.83, True,  False),
    ("AssetsLowRes/CC/icons_resources",             64,  64, 1.00, 1.00, False, False),
]
RES_STATES = ["", "Highlight", "Missing", "Used"]


# ------------------------------------------------------------- background --
def cutout(img: Image.Image, edge_thresh: float = 14.0, feather: float = 1.2) -> Image.Image:
    """Remove the opaque backdrop via edge-barrier region growing from the border."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    h, w, _ = rgb.shape

    # gradient magnitude (forward differences, padded back to full size)
    gx = np.zeros((h, w), np.float32)
    gy = np.zeros((h, w), np.float32)
    gx[:, :-1] = np.abs(rgb[:, 1:] - rgb[:, :-1]).sum(axis=2)
    gy[:-1, :] = np.abs(rgb[1:, :] - rgb[:-1, :]).sum(axis=2)
    grad = np.maximum(gx, gy)

    passable = grad < edge_thresh

    # seed from the image border, then grow while staying on passable pixels
    bg = np.zeros((h, w), bool)
    bg[0, :] = bg[-1, :] = True
    bg[:, 0] = bg[:, -1] = True
    bg &= passable

    # iterative 4-connected dilation, constrained to passable
    while True:
        grown = bg.copy()
        grown[1:, :] |= bg[:-1, :]
        grown[:-1, :] |= bg[1:, :]
        grown[:, 1:] |= bg[:, :-1]
        grown[:, :-1] |= bg[:, 1:]
        grown &= passable
        if grown.sum() == bg.sum():
            break
        bg = grown

    subject = ~bg

    # close pinholes: dilate then erode
    def dil(m, n=1):
        for _ in range(n):
            o = m.copy()
            o[1:, :] |= m[:-1, :]; o[:-1, :] |= m[1:, :]
            o[:, 1:] |= m[:, :-1]; o[:, :-1] |= m[:, 1:]
            m = o
        return m

    def ero(m, n=1):
        return ~dil(~m, n)

    subject = ero(dil(subject, 2), 2)
    # pull the edge inward 1px so feathering samples art colour, not backdrop
    subject = ero(subject, 1)

    alpha = Image.fromarray((subject * 255).astype(np.uint8), "L")
    if feather > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))

    out = img.convert("RGBA")
    out.putalpha(alpha)
    return out


def load_source(path: Path, label: str) -> Image.Image:
    """Open source art, cutting the backdrop ONLY if it does not already have one.

    cutout() rebuilds the alpha channel from scratch and putalpha()s it over
    whatever was there, so running it on art that arrives already keyed throws
    away a better mask than it can compute. The 2026-08-20 art is generated with
    real transparency; the 2026-08-19 art was not. Decide per file instead of
    assuming, so both keep working.
    """
    img = Image.open(path).convert("RGBA")
    a = np.asarray(img.getchannel("A"), np.float32) / 255.0
    clear = float((a < 0.02).mean())
    if clear > 0.15:
        print(f"  {label}: {clear:.0%} already transparent - keeping its own alpha")
        return img
    print(f"  {label}: opaque backdrop - cutting it")
    return cutout(img)


def trim_square(img: Image.Image, pad_frac: float = 0.04) -> Image.Image:
    """Crop to the visible art, then centre it on a square transparent canvas."""
    bbox = img.split()[-1].getbbox()
    if bbox:
        img = img.crop(bbox)
    side = int(max(img.size) * (1 + pad_frac * 2))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    return canvas


# ------------------------------------------------------------------ style --
def _rgb_to_hsv(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx = a[..., :3].max(-1); mn = a[..., :3].min(-1)
    d = mx - mn
    h = np.zeros_like(mx)
    m = d > 1e-6
    rm = m & (mx == r); gm = m & (mx == g) & ~rm; bm = m & (mx == b) & ~rm & ~gm
    h[rm] = ((g - b)[rm] / d[rm]) % 6
    h[gm] = ((b - r)[gm] / d[gm]) + 2
    h[bm] = ((r - g)[bm] / d[bm]) + 4
    h /= 6.0
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0.0)
    return h, s, mx


def _hsv_to_rgb(h, s, v):
    i = np.floor(h * 6.0)
    f = h * 6.0 - i
    p = v * (1 - s); q = v * (1 - f * s); t = v * (1 - (1 - f) * s)
    i = (i.astype(int) % 6)
    out = np.zeros(h.shape + (3,), np.float32)
    for k, (rr, gg, bb) in enumerate([(v, t, p), (q, v, p), (p, v, t),
                                      (p, q, v), (t, p, v), (v, p, q)]):
        m = i == k
        out[m, 0] = rr[m]; out[m, 1] = gg[m]; out[m, 2] = bb[m]
    return out


def house_grade(img: Image.Image, target_brass_sat=0.48, target_other_sat=0.39,
                target_val=0.46) -> Image.Image:
    """
    Pull the artwork onto Larian's measured class-icon grade.

    Targets come from tools/analyze_palette.py run over every vanilla class icon
    in Game.pak, split by hue band (see BRASS_LO/HI):

        band            share      saturation
        brass 15-50     ~80%       0.48
        everything else ~20%       0.39
        mean value                 0.48

    AI art comes back far hotter. v2 of this emblem measured brass 0.68 / other
    0.84 / value 0.39 - a good composition that still reads as foreign next to
    Battle Master because the crimson and violet are neon by comparison.

    Saturation compression is HUE-SELECTIVE - pixels inside the brass band
    (hue ~15-50) get a gentler pull than everything else (the crimson field, the
    violet rift), blended smoothly across the hue boundary so there's no seam.
    That part is load-bearing: an earlier version compressed globally and crushed
    the brass ring to pale grey right along with the neon interior. Brass is what
    makes an icon read as part of the family - it must survive distinctly.

    ITERATION 2 (2026-08-10): the per-band split was right but the CURVE SHAPE
    was wrong, and it produced a "washed out" result - user report, confirmed by
    measurement. The original curve was a GAMMA power law, s -> s**g, solved
    numerically so the mean hit target. Gamma curves are highly non-linear: for
    the "other" band the solver had to push gamma to its cap (8.0) and still
    undershot. At gamma 8, a MIDTONE pixel (s=0.5) collapses to 0.5**8 = 0.004 -
    essentially grey - while only pixels already near s=1 survive with real
    colour. Net effect: most of the crimson/violet area collapsed toward grey
    haze, with only small hot spots staying vivid - which IS what "washed out"
    looks like, not a subtle taste difference.

    Fixed by switching to a PROPORTIONAL (linear) scale, s2 = s * k, where
    k = target/current - an exact, non-iterative solve, since scaling DOWN never
    needs clipping (max output stays <= max input <= 1). This preserves relative
    saturation across the image - a pixel twice as saturated as another stays
    twice as saturated after scaling - so the whole band moves together instead
    of the distribution collapsing into a near-grey clump plus a few hot spots.
    Targets are the real vanilla-measured numbers, unchanged from iteration 1;
    it was only ever the curve shape that was wrong, not the targets.

    Value also had a role: the 0.48 mean-value target needed up to a 1.35x
    brighten, which pushes bright metal highlights toward flat white (blown
    highlights read as washed-out too). Target eased to 0.46 and the brighten
    cap lowered to 1.15x, so highlight detail survives.
    """
    a = np.asarray(img.convert("RGBA"), np.float32) / 255.0
    alpha = a[..., 3]
    mask = alpha > 0.5
    if not mask.any():
        return img
    h, s, v = _rgb_to_hsv(a)

    # how far each pixel's hue sits outside the brass band, 0 = brass, 1 = alien
    deg = h * 360.0
    dist = np.zeros_like(deg)
    dist = np.where(deg < BRASS_LO, BRASS_LO - deg, dist)
    dist = np.where(deg > BRASS_HI, deg - BRASS_HI, dist)
    dist = np.minimum(dist, 360.0 - dist)              # hue is circular
    w = np.clip(dist / BRASS_FALLOFF, 0.0, 1.0)

    brass_px = mask & (w < 0.5)
    other_px = mask & (w >= 0.5)

    def scale_for(sel, target):
        """exact proportional scale so mean(s*k) over `sel` hits `target`"""
        if not sel.any():
            return 1.0
        cur = float(s[sel].mean())
        if cur < 1e-6:
            return 1.0
        return float(np.clip(target / cur, 0.15, 1.5))

    k_brass = scale_for(brass_px, target_brass_sat)
    k_other = scale_for(other_px, target_other_sat)

    # blend the two scales by hue distance so the boundary is smooth, not a seam
    k = k_brass * (1.0 - w) + k_other * w
    s2 = np.clip(s * k, 0.0, 1.0)

    cur_v = float(v[mask].mean())
    vscale = 1.0 if cur_v < 1e-6 else min(1.15, max(0.85, target_val / cur_v))
    v2 = np.clip(v * vscale, 0, 1)

    if brass_px.any():
        print(f"    brass  {100.0 * brass_px.sum() / mask.sum():4.0f}% of px   "
              f"sat {float(s[brass_px].mean()):.2f} -> {float(s2[brass_px].mean()):.2f}  "
              f"(scale {k_brass:.2f}, target {target_brass_sat})")
    if other_px.any():
        print(f"    other  {100.0 * other_px.sum() / mask.sum():4.0f}% of px   "
              f"sat {float(s[other_px].mean()):.2f} -> {float(s2[other_px].mean()):.2f}  "
              f"(scale {k_other:.2f}, target {target_other_sat})")

    rgb = _hsv_to_rgb(h, s2, v2)
    out = np.dstack([rgb, alpha])
    print(f"    value  {cur_v:.2f} -> {float(v2[mask].mean()):.2f}  (target {target_val})")
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8), "RGBA")


def punch(img: Image.Image, contrast=1.14, saturation=1.12, brightness=1.03) -> Image.Image:
    img = ImageEnhance.Contrast(img).enhance(contrast)
    img = ImageEnhance.Color(img).enhance(saturation)
    img = ImageEnhance.Brightness(img).enhance(brightness)
    return img


def rim_glow(img: Image.Image, colour=ACCENT, spread_frac=0.05, strength=0.55) -> Image.Image:
    """Soft coloured light behind the subject so the silhouette separates on a dark panel."""
    if strength <= 0:
        return img
    a = img.split()[-1]
    r = max(2, int(min(img.size) * spread_frac))
    halo = a.filter(ImageFilter.GaussianBlur(r))
    halo = halo.point(lambda v: int(min(255, v * strength * 2.2)))
    glow = Image.new("RGBA", img.size, colour + (0,))
    glow.putalpha(halo)
    return Image.alpha_composite(glow, img)


def tint_state(img: Image.Image, state: str) -> Image.Image:
    """Action-resource pips have four states; derive them from the one artwork."""
    if state == "Highlight":              # hovered / emphasised
        # keep the glow TIGHT - at 48px a wide halo turns the pip into a blob
        out = ImageEnhance.Brightness(img).enhance(1.28)
        return rim_glow(out, ACCENT, 0.035, 0.6)
    if state == "Used":                   # spent this turn
        out = ImageEnhance.Color(img).enhance(0.25)
        return ImageEnhance.Brightness(out).enhance(0.55)
    if state == "Missing":                # not available at all
        out = ImageEnhance.Color(img).enhance(0.08)
        return ImageEnhance.Brightness(out).enhance(0.32)
    return img


def resize(img: Image.Image, size: int) -> Image.Image:
    out = img.resize((size, size), Image.LANCZOS)
    # small icons lose their read without a touch of sharpening
    if size <= 160:
        amount = 85 if size <= 80 else 60
        out = out.filter(ImageFilter.UnsharpMask(radius=1.0, percent=amount, threshold=2))
    return out


def place(img: Image.Image, cw: int, ch: int, h_frac: float,
          max_w_frac: float, y_off_frac: float = 0.0) -> Image.Image:
    """Fit img's OPAQUE CONTENT into a cw x ch canvas the way vanilla frames it.

    `resize()` could only make squares, which is why the non-square 44x64 slot was
    being fed a 48x48 image and why every icon filled its whole canvas. Here the
    art is trimmed to its alpha bounding box first, scaled so that box is h_frac
    of the canvas height (backing off if that would exceed max_w_frac of the
    width), then centred horizontally and offset vertically - vanilla class icons
    sit above centre, they are not centred.
    """
    bb = img.getchannel("A").point(lambda v: 255 if v > 12 else 0).getbbox()
    content = img.crop(bb) if bb else img
    scale = (ch * h_frac) / content.height
    if content.width * scale > cw * max_w_frac:
        scale = (cw * max_w_frac) / content.width
    w, h = max(1, round(content.width * scale)), max(1, round(content.height * scale))
    content = content.resize((w, h), Image.LANCZOS)
    if max(w, h) <= 160:
        amount = 85 if max(w, h) <= 80 else 60
        content = content.filter(ImageFilter.UnsharpMask(radius=1.0, percent=amount, threshold=2))
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    out.paste(content, (round((cw - w) / 2),
                        round((ch - h) / 2 + y_off_frac * ch)), content)
    return out


def place_circle(img: Image.Image, cw: int, ch: int, radius_frac: float) -> Image.Image:
    """Fit img's opaque content inside a CIRCLE, dead centre.

    place() scales by height and offsets upward, which is right for the square
    character-sheet frame and wrong for a round one: a tall emblem framed by
    height pokes out of the circle at top and bottom while leaving the sides
    empty. Here the art is scaled so its furthest opaque pixel from its own
    centroid-of-extent lands exactly on radius_frac x half-canvas, then pasted so
    that centre coincides with the canvas centre.

    Centring is on the alpha BOUNDING BOX centre, not the canvas centre of the
    trimmed image and not the centre of mass. A glow that is brighter on one side
    drags the centre of mass off; the bounding box is what the eye reads as the
    extent of the shape, and it is what vanilla lines up (their hotbar bbox
    centres sit within 3px of the canvas centre).
    """
    bb = img.getchannel("A").point(lambda v: 255 if v > 12 else 0).getbbox()
    content = img.crop(bb) if bb else img

    a = np.asarray(content.getchannel("A"), np.float32) / 255.0
    ys, xs = np.nonzero(a > 0.5)
    if len(xs) == 0:
        return place(img, cw, ch, 0.79, CLASS_MAX_W, 0.0)
    cx, cy = (xs.min() + xs.max()) / 2.0, (ys.min() + ys.max()) / 2.0
    reach = float(np.hypot(ys - cy, xs - cx).max())

    scale = (radius_frac * min(cw, ch) / 2.0) / reach
    w, h = max(1, round(content.width * scale)), max(1, round(content.height * scale))
    content = content.resize((w, h), Image.LANCZOS)
    if max(w, h) <= 160:
        amount = 85 if max(w, h) <= 80 else 60
        content = content.filter(ImageFilter.UnsharpMask(radius=1.0, percent=amount, threshold=2))

    # the bbox centre inside the RESIZED content, so the paste lands it on centre
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    out.paste(content, (round(cw / 2.0 - cx * scale), round(ch / 2.0 - cy * scale)), content)
    return out


def stone_slab(size: int, seed: int = 20260819) -> Image.Image:
    """An ORIGINAL stone-slab background for the level-up resource icon.

    WHY IT EXISTS. Sources (mod.io "Custom Icon Visual Cheat Sheet", TLH_MOD) name
    this slot as the "128x128 Level Up Icon (resource background)", and vanilla
    backs it up: every one of the 21 shipped CC resource icons is 100% opaque, a
    grey stone slab with a simple glowing symbol on it. Ours was a bare die on
    transparency, which is the only resource slot we had structurally wrong.

    WHY IT IS GENERATED rather than lifted. Larian's slab is right there in
    Game.pak and would look perfect. Shipping it would also make the README's "no
    Larian art is copied or redistributed" line false, so it is drawn here instead:
    value noise, smoothed, plus a vignette and a darkened rim.
    """
    rng = np.random.default_rng(seed)
    n = rng.random((size, size)).astype(np.float32)
    # cheap smooth noise: average a few blurred octaves so it reads as stone grain
    base = np.zeros_like(n)
    for oct_, amp in ((1, 0.5), (2, 0.3), (4, 0.2)):
        small = rng.random((max(2, size // (8 * oct_)),) * 2).astype(np.float32)
        up = np.asarray(Image.fromarray((small * 255).astype(np.uint8))
                        .resize((size, size), Image.BICUBIC), np.float32) / 255.0
        base += up * amp
    grain = 0.85 * base + 0.15 * n

    yy, xx = np.mgrid[0:size, 0:size]
    r = np.sqrt(((xx - size / 2) / (size / 2)) ** 2 + ((yy - size / 2) / (size / 2)) ** 2)
    vign = np.clip(1.15 - 0.45 * r, 0, 1)                       # darker toward the edge
    edge = np.minimum.reduce([xx, yy, size - 1 - xx, size - 1 - yy]) / (size * 0.09)
    rim = np.clip(edge, 0, 1) * 0.35 + 0.65                     # worn dark border

    v = np.clip((0.30 + 0.34 * grain) * vign * rim, 0, 1)
    rgb = np.stack([v * 1.00, v * 0.97, v * 0.92], -1)          # faintly warm grey
    out = np.concatenate([rgb, np.ones((size, size, 1), np.float32)], -1)
    return Image.fromarray((out * 255).astype(np.uint8))


# ------------------------------------------------------------------- emit --
def save_png(img: Image.Image, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")


def to_dds(png: Path, dds_dir: Path):
    """
    RGBA -> BC7_UNORM. NOT the _SRGB variant - see the long note below, this
    reverses what Larian's own "Adding Skill and Item Icons" guide's snippet
    implies (RGBA+sRGB -> BC7_UNORM_SRGB) and it matters.

    FIXED 2026-08-10 after a live report: "still has a severe washed out
    appearance, almost like it has a white overlay on it in game." Root-caused,
    not re-tuned by feel - read every UI icon DDS's header directly (DX10
    extended header, dxgiFormat field; tools/dds_format.py) rather than trusting
    a conversion snippet's pseudocode:

        source                                    class icon    resource icon
        vanilla, Game.pak (Fighter, BattleMaster)  BC7_UNORM     BC7_UNORM
        vanilla, Game.pak (BardicInspiration, ...) -             BC7_UNORM
        Expansion.pak (3rd-party class mod)        BC7_UNORM_SRGB (same bug)
        ours, before this fix                      BC7_UNORM_SRGB BC7_UNORM_SRGB

    100% of GENUINE vanilla icons checked, both categories, use plain BC7_UNORM
    - never the _SRGB variant. The _SRGB DXGI format tells the GPU to
    auto-decode gamma on every sample; if BG3's UI shader ALSO applies its own
    gamma/tone handling to icon textures (plausible - UI compositing is
    typically done in display-referred/gamma space, not linear), an _SRGB
    texture gets gamma-decoded TWICE. Doubling a gamma curve lifts blacks and
    midtones hard toward white while barely moving true white or true black -
    exactly a "washed out, like a white overlay" look, and exactly why it hit
    our red/white/violet palette much harder than Expansion's icon (whose
    example mod was ALSO on the wrong format the whole time - the guide
    snippet's sRGBBool conditional matched what Larian's OWN toolkit outputs by
    default, but not what the actual vanilla UI pipeline wants for icons).

    Was applied to BOTH class and resource icon output, matching vanilla ground
    truth in both categories - not just the one that was reported broken.
    """
    dds_dir.mkdir(parents=True, exist_ok=True)
    tmp = OBJ / "ddstmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(TEXCONV), "-f", "BC7_UNORM", "-y", "-m", "1",
                        "-o", str(tmp), str(png)],
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"texconv failed for {png}:\n{r.stdout}\n{r.stderr}")
    produced = next(iter(tmp.glob("*.dds")), None)
    if produced is None:
        raise SystemExit(f"texconv produced no dds for {png}")
    shutil.copy(produced, dds_dir / (png.stem + ".DDS"))


def main():
    ap = argparse.ArgumentParser()
    # Vanilla class icons have NO outer glow anywhere in the set, so glow is now
    # opt-in rather than default. --no-glow kept as a no-op for older muscle memory.
    ap.add_argument("--glow", action="store_true", help="add a rim glow (off-style, off by default)")
    ap.add_argument("--no-glow", action="store_true", help=argparse.SUPPRESS)
    # DEFAULT FLIPPED 2026-08-19 (session 49), user decision after a side-by-side.
    # The house grade was tuned in session 45 against the PREVIOUS artwork, which
    # needed pulling toward Larian's brass band. The current art already carries the
    # brass ring and strong values, so the grade now subtracts the colour that makes
    # the icon identifiable - at 70px hotbar size the graded version reads as a brass
    # ring with a dark smudge, the ungraded one keeps a legible red/purple core.
    # Grading is opt-in now so a future re-run cannot silently undo this.
    ap.add_argument("--grade", action="store_true",
                    help="apply the house-style colour grade (OFF by default - the "
                         "current art does not need it; see the note in the source)")
    args = ap.parse_args()

    for t in (TEXCONV, DIVINE):
        if not t.exists():
            raise SystemExit(f"required tool missing: {t}")
    required = [CLASS_SRC, HOTBAR_SRC] + ([RES_SRC] if HAS_RESOURCE else [])
    for s in required:
        if not s.exists():
            raise SystemExit(f"source art missing: {s}")

    if OBJ.exists():
        shutil.rmtree(OBJ)
    OBJ.mkdir(parents=True, exist_ok=True)

    entries = []  # (MapKey, size)

    # ---------------------------------------------------------- class icon --
    # TWO MASTERS FROM TWO ARTWORKS, keyed by folder. See the long note above
    # CLASS_TARGETS: the character-sheet slots and the hotbar slots are separate
    # surfaces and the game ships different art in each. Selecting by size instead
    # of folder is what leaked a hotbar variant onto the character sheet before.
    print("class icon:")
    class_cut = load_source(CLASS_SRC, "class emblem")
    print("hotbar icon:")
    hotbar_cut = load_source(HOTBAR_SRC, "hotbar emblem")
    if args.grade:
        class_cut = house_grade(class_cut)
        hotbar_cut = house_grade(hotbar_cut)

    # The medallion gets NO padding and a harder contrast curve: it goes down to
    # 72px, where anything soft dissolves. place_circle() sharpens again after
    # resizing, on top of this.
    masters = {
        "class":  punch(trim_square(class_cut,  pad_frac=0.04), contrast=1.10, saturation=1.0),
        "hotbar": punch(trim_square(hotbar_cut, pad_frac=0.00), contrast=1.16, saturation=1.0),
    }
    if args.glow:
        masters["class"] = rim_glow(masters["class"], ACCENT, 0.045, 0.5)
        masters["hotbar"] = rim_glow(masters["hotbar"], ACCENT, 0.03, 0.35)
    for k, m in masters.items():
        save_png(m, OBJ / f"{k}_master.png")
        print(f"  {k} master {m.width}x{m.height}")

    for rel, cw, ch, register, hf, yo, src, circle in CLASS_TARGETS:
        img = (place_circle(masters[src], cw, ch, circle) if circle
               else place(masters[src], cw, ch, hf, CLASS_MAX_W, yo))
        png = GUI / rel / f"{CLASS_NAME}.png"
        save_png(img, png)
        to_dds(png, GUI / rel)
        if register:
            entries.append((f"{rel}/{CLASS_NAME}.png", cw, ch))
        how = f"circle r={circle}" if circle else f"content {hf:.0%}h, y{yo:+.1%}"
        print(f"  {rel:<34} {cw}x{ch}  <- {src} master, {how}")

    if not HAS_RESOURCE:
        print("resource icon: SKIPPED - this mod ships no ActionResourceDefinitions")
    else:
        # ------------------------------------------------------- resource icon --
        print("resource icon:")
        res = trim_square(load_source(RES_SRC, "warp die"))
        res = punch(res, contrast=1.18, saturation=1.15)
        save_png(res, OBJ / "resource_master.png")
        print(f"  master {res.width}x{res.height}")

        for rel, cw, ch, hf, mw, has_states, register in RES_TARGETS:
            for state in (RES_STATES if has_states else [""]):
                styled = tint_state(res, state)
                if "icons_resources" in rel:
                    # the level-up slot is a slab with the symbol ON it, not a cutout
                    img = stone_slab(cw)
                    die = place(styled, cw, ch, 0.62, 0.62)
                    img.alpha_composite(die)
                else:
                    img = place(styled, cw, ch, hf, mw)
                d = f"{rel}/{state}" if state else rel
                png = GUI / d / f"{RES_NAME}.png"
                save_png(img, png)
                to_dds(png, GUI / d)
                if register:
                    entries.append((f"{d}/{RES_NAME}.png", cw, ch))
                print(f"  {d:<52} {cw}x{ch}")

    # ---------------------------------------------------------- metadata --
    print("writing GUI/metadata.lsf ...")
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<save>",
             '    <version major="4" minor="8" revision="0" build="500" />',
             '    <region id="config">', '        <node id="config">',
             "            <children>", '                <node id="entries">',
             "                    <children>"]
    for key, ew, eh in entries:
        lines += [
            '                        <node id="Object">',
            f'                            <attribute id="MapKey" type="FixedString" value="{key}" />',
            "                            <children>",
            '                                <node id="entries">',
            f'                                    <attribute id="h" type="int16" value="{eh}" />',
            '                                    <attribute id="mipcount" type="int8" value="1" />',
            f'                                    <attribute id="w" type="int16" value="{ew}" />',
            "                                </node>", "                            </children>",
            "                        </node>",
        ]
    lines += ["                    </children>", "                </node>",
              "            </children>", "        </node>", "    </region>", "</save>"]

    lsx = OBJ / "metadata.lsx"
    lsx.write_text("\n".join(lines), encoding="utf-8")
    lsf = GUI / "metadata.lsf"
    r = subprocess.run([str(DIVINE), "-g", "bg3", "-a", "convert-resource",
                        "-s", str(lsx), "-d", str(lsf), "-i", "lsx", "-o", "lsf"],
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not lsf.exists():
        raise SystemExit(f"convert-resource failed:\n{r.stdout}\n{r.stderr}")

    print(f"  metadata.lsf {lsf.stat().st_size} bytes, {len(entries)} entries")
    print("\ndone. run build.ps1 to pack and deploy.")


if __name__ == "__main__":
    main()
