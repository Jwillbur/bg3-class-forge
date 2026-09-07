#!/usr/bin/env python3
"""Contact sheet so the icons can be eyeballed at real size on a dark UI panel."""
from pathlib import Path
from PIL import Image, ImageDraw
import sys

# --- mod config -----------------------------------------------------------
# Anchored on the MOD BEING AUDITED (the cwd), not on this file. Until
# 2026-09-06 every path below was the literal string CFG.name, so running
# this from another mod silently audited Warpblade instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402
CFG = modconfig.load(Path.cwd())


MODROOT = Path(__file__).resolve().parent.parent
GUI = MODROOT / "Mods" / CFG.name / "GUI"
OUT = MODROOT / "obj" / "icons" / "preview.png"

BG = (24, 26, 31)
FG = (196, 202, 212)

rows = [
    ("class 300 (character sheet)", GUI / "Assets/ClassIcons/Warpblade.png", 1),
    ("class 140 (hotbar)",          GUI / "Assets/ClassIcons/hotbar/Warpblade.png", 1),
    ("class 70 (lowres hotbar)",    GUI / "AssetsLowRes/ClassIcons/hotbar/Warpblade.png", 1),
]
states = [
    ("available", GUI / "Assets/ActionResources_c/Icons/Resources/WarpDie.png"),
    ("highlight", GUI / "Assets/ActionResources_c/Icons/Resources/Highlight/WarpDie.png"),
    ("used",      GUI / "Assets/ActionResources_c/Icons/Resources/Used/WarpDie.png"),
    ("missing",   GUI / "Assets/ActionResources_c/Icons/Resources/Missing/WarpDie.png"),
]

W, H = 900, 660
sheet = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(sheet)

x = 30
for label, p, _ in rows:
    im = Image.open(p).convert("RGBA")
    sheet.paste(im, (x, 40), im)
    d.text((x, 40 + im.height + 10), label, fill=FG)
    x += im.width + 40

# resource pips: real 48px, and a 3x blow-up to inspect detail
y = 400
d.text((30, y - 24), "WarpDie action-resource states  (real 48px, then 3x)", fill=FG)
x = 30
for label, p in states:
    im = Image.open(p).convert("RGBA")
    sheet.paste(im, (x, y), im)
    big = im.resize((144, 144), Image.NEAREST)
    sheet.paste(big, (x, y + 64), big)
    d.text((x, y + 216), label, fill=FG)
    x += 170

OUT.parent.mkdir(parents=True, exist_ok=True)
sheet.save(OUT)
print(OUT)
