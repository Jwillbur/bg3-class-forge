#!/usr/bin/env python3
"""Warpblade's class icon next to the vanilla Fighter subclasses, at picker sizes.

The real question is never "does it look good on its own" - it is "does it look
like it belongs on the same row as Battle Master". This renders that row.
"""
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


REF = Path(r"C:\Modding\bg3_iconref\vanilla")
MINE = Path(__file__).resolve()CFG.mods / "GUI/Assets/ClassIcons/Warpblade.png"
OUT = Path(__file__).resolve().parent.parent / "obj/icons/family.png"

BG = (24, 26, 31)
FG = (200, 205, 215)
HI = (120, 190, 255)

order = ["BattleMaster", "Champion", "EldritchKnight", "Fighter", None]

cell = 170
W = cell * len(order) + 40
H = 340
sheet = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(sheet)
d.text((20, 8), "does it belong on this row?  (150px, then 70px as shown in the picker)", fill=FG)

x = 20
for name in order:
    if name is None:
        p, label, col = MINE, "Warpblade (ours)", HI
    else:
        p, label, col = REF / f"{name}.png", name, FG
    if not p.exists():
        x += cell
        continue
    im = Image.open(p).convert("RGBA")
    big = im.resize((150, 150), Image.LANCZOS)
    sheet.paste(big, (x, 40), big)
    sm = im.resize((70, 70), Image.LANCZOS)
    sheet.paste(sm, (x + 40, 210), sm)
    d.text((x, 196), label, fill=col)
    x += cell

OUT.parent.mkdir(parents=True, exist_ok=True)
sheet.save(OUT)
print(OUT)
