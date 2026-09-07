#!/usr/bin/env python3
"""
feature_icons.py - ship Warpblade's own art for a PassiveData/SpellData icon.

WHY THIS EXISTS, AND WHY make_icons.py COULD NOT DO IT
------------------------------------------------------
There are THREE unrelated icon systems in BG3 and this is the third one.

  1. ActionResourceDefinition name -> loose DDS under GUI/Assets/...  (the Warp
     Dice bar on the hotbar).                       make_icons.py, RES_TARGETS
  2. ClassDescription name -> GUI/Assets/ClassIcons/ (the subclass emblem).
                                                    make_icons.py, CLASS_TARGETS
  3. PassiveData / SpellData `data "Icon"` -> THIS FILE.

Number 3 is what the character sheet's feature list, the level-up feature list
and every tooltip actually read. `Warpblade_WarpDice` carried
`data "Icon" "PassiveFeature_Generic_Magical"` - vanilla's blue sparkles - which
is the "starry icon" that kept showing up next to a perfectly good Warp Die
everywhere else.

WHERE THE FILES GO, MEASURED FROM Game.pak 2026-08-20
-----------------------------------------------------
An earlier note in tools/icon_atlas.py claimed the game "ships NO loose files
for vanilla spell icons - only 64x64 atlas tiles", and concluded the 380/144
files in the community guide were unnecessary. THAT WAS WRONG, and it is why
shipping an atlas alone never fixed a tooltip. Game.pak actually carries:

    Public/Game/GUI/Assets/Tooltips/Icons/               1521 files, 380x380
    Public/Game/GUI/AssetsLowRes/Tooltips/Icons/         1514 files, 192x192
    Public/Game/GUI/Assets/ControllerUIIcons/skills_png/ 1522 files, 144x144
    Public/Game/GUI/AssetsLowRes/.../skills_png/         1515 files,  72x72

All BC7_UNORM (DXGI 98), mipcount 1. The LowRes sizes are not in the guide -
it lists 380 and 144 only - and guessing them is exactly the mistake that put
150/70 in the class-icon table when vanilla wanted 152/72.

The `Public/Game/...` prefix is not a typo. Three independent working mods on
this machine all place feature icons there rather than under their own
namespace: DEchoKnight (16), CommunityLibrary (35), Expansion (131).

The atlas trio is copied from DEchoKnight, which demonstrably works in game,
NOT from the guide - the two disagree on all three names:
    Public/<Mod>/Assets/Textures/Icons/Icons_<Mod>.DDS   (guide: lowercase .dds)
    Public/<Mod>/GUI/Icons_Skills.lsx                    (guide: Icons_<Mod>.lsx)
    Public/<Mod>/Content/UI/[PAK]_UI/Icons_<Mod>.lsf     (guide: _merged.lsx)

FRAMING is vanilla's too: tooltip content spans 0.92-0.97 of the canvas, near
full bleed, glowing glyph on transparency with no backing plate.

Usage:  py tools/feature_icons.py [--check]
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

# --- mod config -----------------------------------------------------------
# Anchored on the MOD BEING AUDITED (the cwd), not on this file. Until
# 2026-09-06 every path below was the literal string CFG.name, so running
# this from another mod silently audited Warpblade instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402
CFG = modconfig.load(Path.cwd())


HERE = Path(__file__).resolve().parent
MOD = HERE.parent
OBJ = MOD / "obj" / "feature_icons"

TEXCONV = Path(r"C:\Modding\tools\texconv\texconv.exe")
DIVINE = Path(r"C:\Modding\tools\lslib\Packed\Tools\Divine.exe")

# icon name as written in `data "Icon"`  ->  source artwork
FEATURE_ICONS = {
    "Warpblade_WarpDice": MOD / "art" / "resource.png",
}

# (path under the pak root, edge px, content height fraction)
LOOSE_TARGETS = [
    ("Public/Game/GUI/Assets/Tooltips/Icons",                380, 0.92),
    ("Public/Game/GUI/AssetsLowRes/Tooltips/Icons",          192, 0.92),
    ("Public/Game/GUI/Assets/ControllerUIIcons/skills_png",  144, 0.92),
    ("Public/Game/GUI/AssetsLowRes/ControllerUIIcons/skills_png", 72, 0.92),
]

TILE, GRID = 64, 8
ATLAS_PX = TILE * GRID
ATLAS_DDS = CFG.public / "Assets/Textures/Icons/Icons_Warpblade.DDS"
UVMAP = CFG.public / "GUI/Icons_Skills.lsx"
TEXBANK = CFG.public / "Content/UI/[PAK]_UI/Icons_Warpblade.lsf"
# Stable across rebuilds so a redeploy does not orphan the previous resource.
# Shared with tools/icon_atlas.py, which is why that script now refuses to run.
ATLAS_UUID = "b3d41f27-8a05-4c6e-9f13-2ad7e0c54b91"

STATS = CFG.stats


# --------------------------------------------------------------- imaging --
def fit(img: Image.Image, edge: int, h_frac: float) -> Image.Image:
    """Trim to the art, scale to h_frac of the canvas, centre it."""
    bb = img.getchannel("A").point(lambda v: 255 if v > 12 else 0).getbbox()
    art = img.crop(bb) if bb else img
    scale = (edge * h_frac) / max(art.width, art.height)
    w, h = max(1, round(art.width * scale)), max(1, round(art.height * scale))
    art = art.resize((w, h), Image.LANCZOS)
    if edge <= 144:
        art = art.filter(ImageFilter.UnsharpMask(radius=1.0, percent=70, threshold=2))
    out = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    out.paste(art, ((edge - w) // 2, (edge - h) // 2), art)
    return out


def to_dds(png: Path, dest_dir: Path, name: str) -> Path:
    """BC7_UNORM, no mips - matching every vanilla feature icon measured.

    texconv names its output after the input stem and picks its own extension
    case. Both are corrected here, because BG3 resolves paths inside a pak
    case-sensitively while Windows hides the difference from every check that
    might have caught it. That exact mismatch shipped a blank atlas once.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(TEXCONV), "-nologo", "-y", "-f", "BC7_UNORM", "-m", "1",
                        "-ft", "dds", "-o", str(dest_dir), str(png)],
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"texconv failed on {png.name}:\n{r.stdout}\n{r.stderr}")
    made = next((p for p in dest_dir.iterdir()
                 if p.is_file() and p.stem == png.stem
                 and p.suffix.lower() == ".dds"), None)
    if made is None:
        raise SystemExit(f"texconv produced no dds for {png.name} in {dest_dir}")
    want = dest_dir / f"{name}.DDS"
    if made != want:
        tmp = dest_dir / f"_{name}.tmp"
        made.replace(tmp)
        tmp.replace(want)
    return want


# ---------------------------------------------------------------- wiring --
def write_uvmap(placed: dict[str, tuple[int, int]]) -> None:
    rows = []
    for name, (cx, cy) in sorted(placed.items(), key=lambda kv: kv[1][1] * GRID + kv[1][0]):
        u1, v1 = cx / GRID, cy / GRID
        rows.append(
            '                <node id="IconUV">\n'
            f'                    <attribute id="MapKey" type="FixedString" value="{name}"/>\n'
            f'                    <attribute id="U1" type="float" value="{u1:.8f}"/>\n'
            f'                    <attribute id="U2" type="float" value="{u1 + 1 / GRID:.8f}"/>\n'
            f'                    <attribute id="V1" type="float" value="{v1:.8f}"/>\n'
            f'                    <attribute id="V2" type="float" value="{v1 + 1 / GRID:.8f}"/>\n'
            '                </node>')
    UVMAP.parent.mkdir(parents=True, exist_ok=True)
    UVMAP.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<save>\n'
        '    <version major="4" minor="2" revision="0" build="300"/>\n'
        '    <region id="TextureAtlasInfo">\n        <node id="root">\n            <children>\n'
        '                <node id="TextureAtlasIconSize">\n'
        f'                    <attribute id="Height" type="int32" value="{TILE}"/>\n'
        f'                    <attribute id="Width" type="int32" value="{TILE}"/>\n'
        '                </node>\n'
        '                <node id="TextureAtlasPath">\n'
        '                    <attribute id="Path" type="string" '
        'value="Assets/Textures/Icons/Icons_Warpblade.DDS"/>\n'
        f'                    <attribute id="UUID" type="FixedString" value="{ATLAS_UUID}"/>\n'
        '                </node>\n'
        '                <node id="TextureAtlasTextureSize">\n'
        f'                    <attribute id="Height" type="int32" value="{ATLAS_PX}"/>\n'
        f'                    <attribute id="Width" type="int32" value="{ATLAS_PX}"/>\n'
        '                </node>\n'
        '            </children>\n        </node>\n    </region>\n'
        '    <region id="IconUVList">\n        <node id="root">\n            <children>\n'
        + "\n".join(rows) + "\n"
        '            </children>\n        </node>\n    </region>\n</save>\n', encoding="utf-8")


def write_texbank() -> None:
    """TextureBank entry as .lsf. Resource.ID must equal the UV map's UUID -
    that pairing is the only thing linking the two files."""
    OBJ.mkdir(parents=True, exist_ok=True)
    lsx = OBJ / "_texbank.lsx"
    lsx.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<save>\n'
        '\t<version major="4" minor="0" revision="9" build="328"/>\n'
        '\t<region id="TextureBank">\n\t\t<node id="TextureBank">\n\t\t\t<children>\n'
        '\t\t\t\t<node id="Resource">\n'
        f'\t\t\t\t\t<attribute id="ID" type="FixedString" value="{ATLAS_UUID}"/>\n'
        '\t\t\t\t\t<attribute id="Localized" type="bool" value="False"/>\n'
        '\t\t\t\t\t<attribute id="Name" type="LSString" value="Icons_Warpblade"/>\n'
        '\t\t\t\t\t<attribute id="SRGB" type="bool" value="True"/>\n'
        '\t\t\t\t\t<attribute id="SourceFile" type="LSString" '
        'value=str(CFG.public / "Assets/Textures/Icons/Icons_Warpblade.DDS")/>\n'
        '\t\t\t\t\t<attribute id="Streaming" type="bool" value="True"/>\n'
        '\t\t\t\t\t<attribute id="Template" type="FixedString" value="Icons_Skills"/>\n'
        '\t\t\t\t\t<attribute id="Type" type="int32" value="0"/>\n'
        '\t\t\t\t</node>\n\t\t\t</children>\n\t\t</node>\n\t</region>\n</save>\n',
        encoding="utf-8")
    TEXBANK.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(DIVINE), "-g", "bg3", "-a", "convert-resource",
                        "-s", str(lsx), "-d", str(TEXBANK), "-o", "lsf"],
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"lsf conversion failed: {(r.stderr or r.stdout)[:300]}")
    # Leave no intermediate .lsx carrying a live-looking UUID that nothing ships;
    # validate.py's cross-reference check reports one as a dangling GUID.
    lsx.unlink(missing_ok=True)


# ------------------------------------------------------------------ check --
def check() -> int:
    """Every icon name a stats entry asks for must exist somewhere we ship,
    or be a vanilla name. Only OUR names are checked - a vanilla name that
    resolves in game is not this tool's business."""
    bad = 0
    for f in sorted(STATS.glob("*.txt")):
        for n, line in enumerate(io.open(f, encoding="utf-8").read().splitlines(), 1):
            m = re.match(r'\s*data\s+"Icon"\s+"([^"]+)"', line)
            if not m or not m.group(1).startswith("Warpblade_"):
                continue
            name = m.group(1)
            missing = [d for d, _, _ in LOOSE_TARGETS
                       if not (MOD / d / f"{name}.DDS").is_file()]
            if name not in FEATURE_ICONS:
                print(f"ERROR {f.name}:{n} icon {name!r} is ours but nothing builds it")
                bad += 1
            elif missing:
                print(f"ERROR {f.name}:{n} icon {name!r} missing from: {', '.join(missing)}")
                bad += 1
    print(f"\n{bad} problem(s)" if bad else "\nclean - every Warpblade_* icon name ships art")
    return 1 if bad else 0


# ------------------------------------------------------------------- main --
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="verify shipped art matches what the stats ask for, build nothing")
    args = ap.parse_args()
    if args.check:
        return check()

    for t in (TEXCONV, DIVINE):
        if not t.exists():
            raise SystemExit(f"required tool missing: {t}")
    OBJ.mkdir(parents=True, exist_ok=True)

    atlas = Image.new("RGBA", (ATLAS_PX, ATLAS_PX), (0, 0, 0, 0))
    placed: dict[str, tuple[int, int]] = {}

    for slot, (name, src) in enumerate(sorted(FEATURE_ICONS.items())):
        if not src.exists():
            raise SystemExit(f"source art missing: {src}")
        img = Image.open(src).convert("RGBA")
        a = np.asarray(img.getchannel("A"), np.float32) / 255.0
        if (a < 0.02).mean() <= 0.15:
            raise SystemExit(
                f"{src.name} has no transparent background. Feature icons composite "
                f"over the tooltip panel; an opaque one shows as a card behind the art.")
        print(f"{name}  <- {src.name}")

        for rel, edge, hf in LOOSE_TARGETS:
            png = OBJ / f"{name}_{edge}.png"
            fit(img, edge, hf).save(png)
            out = to_dds(png, MOD / rel, name)
            print(f"  {rel:<58} {edge}x{edge}")
            if not out.is_file():
                raise SystemExit(f"expected {out} on disk")

        cx, cy = slot % GRID, slot // GRID
        atlas.alpha_composite(fit(img, TILE, hf), (cx * TILE, cy * TILE))
        placed[name] = (cx, cy)
        print(f"  atlas tile ({cx},{cy})                                       {TILE}x{TILE}")

    apng = OBJ / "_atlas.png"
    atlas.save(apng)
    to_dds(apng, ATLAS_DDS.parent, ATLAS_DDS.stem)
    print(f"\natlas   -> {ATLAS_DDS.relative_to(MOD)}  {ATLAS_PX}x{ATLAS_PX}, "
          f"{len(placed)} of {GRID * GRID} slots")
    write_uvmap(placed)
    print(f"uv map  -> {UVMAP.relative_to(MOD)}")
    write_texbank()
    print(f"texbank -> {TEXBANK.relative_to(MOD)}  ({TEXBANK.stat().st_size} bytes)")
    print("\ndone. run build.ps1 to pack and deploy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
