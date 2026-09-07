"""
Build Warpblade's own icon atlas: vanilla icons, recoloured to the class palette.

WHY. The mod referenced 13 stock vanilla icon names and shipped no spell art of
its own, which reads exactly like what it is - borrowed. This lifts those 13 out
of the shipped atlases, recolours them to the palette of the user's own class
icon, and ships them as Warpblade's own atlas under Warpblade_* names.

THE PALETTE IS MEASURED, NOT CHOSEN. Sampled from art/class-icon-source.png:
violet energy at hue 269 deg (S 0.92, V 0.95 -> #7f12f2) arcing through red-hot
cracked stone at hue 11 deg. That is the same structure a vanilla spell icon
already has - a glowing glyph on a stone plate - so the recolour is a hue
rotation per zone rather than a repaint.

FOUR THINGS THIS GOT WRONG FIRST, all kept as comments where they bit:
  1. Rebuilding the glow (boost value, force saturation, carve a white core)
     looked worse than vanilla every time. Vanilla's core/halo structure is
     already right; only the hue was wrong. Minimal intervention wins.
  2. The core mask has to be measured on the FINAL brightness, not the input.
     Boosting first and thresholding the old value turned all 13 icons white.
  3. Hue is CIRCULAR. Lerping 11 -> 269 linearly travels the long way through
     green and yellow, which is precisely the fringing that appeared on every
     glyph edge. The short arc goes through magenta.
  4. The tutorial's 380x380 tooltip and 144x144 controller files are for art you
     drew yourself. The game ships NO loose files for vanilla spell icons - only
     64x64 atlas tiles, verified by listing Icons.pak, Game.pak and Assets.pak -
     so vanilla tooltips already render from the atlas. Shipping only the atlas
     therefore matches vanilla quality exactly; generating 380s by upscaling 64px
     source would be strictly worse.

The output wiring is copied from a mod that demonstrably works in game rather
than from the wiki, because the two disagree: the UV map is GUI/Icons_Skills.lsx
(not Icons_<Mod>.lsx), the registration is Content/UI/[PAK]_UI/Icons_<Mod>.lsf
(not _merged.lsx), and the DDS extension is uppercase.

    py icon_atlas.py              # extract, recolour, build atlas + wiring
    py icon_atlas.py --preview    # write a before/after sheet, touch nothing else
    py icon_atlas.py --names      # print the icon rename map and exit
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# --- mod config -----------------------------------------------------------
# Anchored on the MOD BEING AUDITED (the cwd), not on this file. Until
# 2026-09-06 every path below was the literal string CFG.name, so running
# this from another mod silently audited Warpblade instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402
CFG = modconfig.load(Path.cwd())


MOD = CFG.root
UNP = Path(r"C:\Modding\bg3_unpacked")
TEXCONV = Path(r"C:\Modding\tools\texconv\texconv.exe")
DIVINE = Path(r"C:\Modding\tools\lslib\Packed\Tools\Divine.exe")
OBJ = MOD / "obj/atlas"

ATLAS_DDS = CFG.public / "Assets/Textures/Icons/Icons_Warpblade.DDS"
UVMAP = CFG.public / "GUI/Icons_Skills.lsx"
TEXBANK = CFG.public / "Content/UI/[PAK]_UI/Icons_Warpblade.lsf"
# Stable, so redeploys do not orphan the previous resource.
ATLAS_UUID = "b3d41f27-8a05-4c6e-9f13-2ad7e0c54b91"

# Which vanilla atlas indexes which dds.
SOURCES = [
    (UNP / "Shared/Public/Shared/GUI/Icons_Skills.lsx",
     UNP / "Icons/Public/Shared/Assets/Textures/Icons/Icons_Skills.dds"),
    (UNP / "Shared/Public/SharedDev/GUI/Icons_Skills.lsx",
     UNP / "Icons/Public/SharedDev/Assets/Textures/Icons/Icons_Skills.dds"),
    (UNP / "GustavX/Public/GustavX/GUI/Icons.lsx",
     UNP / "Icons/Public/GustavX/Assets/Textures/Icons/Icons.dds"),
]

# vanilla icon name -> ours. 1:1, so which icon sits on which feature is unchanged
# and this pass is purely a recolour.
RENAME = {
    "PassiveFeature_MistyEscape":        "Warpblade_MistyEscape",
    "PassiveFeature_Generic_Magical":    "Warpblade_GenericMagical",
    "Action_MenacingAttack_Melee":       "Warpblade_MenacingAttack",
    "Action_FeintingAttack":             "Warpblade_FeintingAttack",
    "Spell_Transmutation_Longstrider":   "Warpblade_Longstrider",
    "Spell_Transmutation_Blink_Teleport": "Warpblade_BlinkTeleport",
    "Spell_Transmutation_Blink":         "Warpblade_Blink",
    "Spell_Evocation_BoomingBlade":      "Warpblade_BoomingBlade",
    "Spell_Conjuration_MistyStep":       "Warpblade_MistyStep",
    "PassiveFeature_Sentinel_ZeroSpeed": "Warpblade_ZeroSpeed",
    "Action_ShadowStrike":               "Warpblade_ShadowStrike",
    "Action_PushingAttack_Melee":        "Warpblade_PushingAttack",
    "Spell_Evocation_ShockingGrasp":     "Warpblade_ShockingGrasp",
}

TILE = 64
GRID = 8                      # 8x8 = 64 slots in a 512x512 atlas
ATLAS_PX = TILE * GRID

GLYPH_HUE = 269.2 / 360.0     # the class icon's energy core
PLATE_HUE = 10.6 / 360.0      # its cracked-stone interior
PLATE_SAT = 0.65              # chosen by eye from a 0.65/0.80/1.00 comparison
GLYPH_FLOOR = 0.70            # so near-white vanilla glyphs take the colour at all
CORE_LO = 0.97                # above this brightness the glyph stays white-hot

NODE_RE = re.compile(r'<node id="IconUV">(.*?)</node>', re.S)
ATTR_RE = re.compile(r'id="(\w+)"[^>]*value="([^"]*)"')


# ------------------------------------------------------------ colour ------
def rgb_to_hsv(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx, mn = a[..., :3].max(-1), a[..., :3].min(-1)
    d = mx - mn
    h = np.zeros_like(mx)
    m = d > 1e-6
    for sel, expr in (((mx == r) & m, lambda: ((g - b) / d) % 6),
                      ((mx == g) & m, lambda: ((b - r) / d) + 2),
                      ((mx == b) & m, lambda: ((r - g) / d) + 4)):
        with np.errstate(invalid="ignore", divide="ignore"):
            h[sel] = expr()[sel]
    return h / 6.0, np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0), mx


def hsv_to_rgb(h, s, v):
    i = np.floor(h * 6).astype(int) % 6
    f = h * 6 - np.floor(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    out = np.zeros(h.shape + (3,), np.float32)
    for k, trio in enumerate([(v, t, p), (q, v, p), (p, v, t),
                              (p, q, v), (t, p, v), (v, p, q)]):
        m = i == k
        out[m] = np.stack(trio, -1)[m]
    return out


def smoothstep(x, a, b):
    t = np.clip((x - a) / max(b - a, 1e-6), 0, 1)
    return t * t * (3 - 2 * t)


def recolour(img: Image.Image) -> Image.Image:
    a = np.asarray(img.convert("RGBA")).astype(np.float32) / 255.0
    rgb, al = a[..., :3], a[..., 3]
    h, s, v = rgb_to_hsv(rgb)

    lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    solid = al > 0.5
    if solid.sum() < 16:
        return img
    # The glyph is simply the part that glows: brighter than its own plate.
    glyph = smoothstep(lum, np.percentile(lum[solid], 55),
                       np.percentile(lum[solid], 92))

    # Shortest arc, not a linear lerp - see note 3 in the docstring.
    d = (GLYPH_HUE - PLATE_HUE + 0.5) % 1.0 - 0.5
    new_h = (PLATE_HUE + d * glyph) % 1.0

    core = smoothstep(v, CORE_LO, 1.0)
    floor = GLYPH_FLOOR * glyph * (1 - core)
    new_s = np.clip(np.maximum(s * (PLATE_SAT + (1 - PLATE_SAT) * glyph), floor), 0, 1)

    out = hsv_to_rgb(new_h, new_s, v)
    return Image.fromarray((np.dstack([out, al]) * 255).round().astype(np.uint8), "RGBA")


# ------------------------------------------------------------ extract -----
def uvmap(path: Path) -> dict:
    out = {}
    for blk in NODE_RE.findall(path.read_text(encoding="utf-8-sig", errors="replace")):
        at = dict(ATTR_RE.findall(blk))
        if "MapKey" in at:
            out[at["MapKey"]] = tuple(float(at[k]) for k in ("U1", "U2", "V1", "V2"))
    return out


def extract_tiles() -> dict[str, Image.Image]:
    OBJ.mkdir(parents=True, exist_ok=True)
    tiles: dict[str, Image.Image] = {}
    for lsx, dds in SOURCES:
        if not lsx.is_file() or not dds.is_file():
            continue
        m = uvmap(lsx)
        want = [k for k in RENAME if k in m and k not in tiles]
        if not want:
            continue
        png = OBJ / f"{dds.stem}_{lsx.parent.parent.name}.png"
        if not png.is_file():
            subprocess.run([str(TEXCONV), "-ft", "png", "-o", str(OBJ), "-y", str(dds)],
                           capture_output=True, check=False)
            (OBJ / f"{dds.stem}.png").replace(png)
        img = Image.open(png).convert("RGBA")
        W, H = img.size
        for name in want:
            u1, u2, v1, v2 = m[name]
            crop = img.crop((round(u1 * W), round(v1 * H), round(u2 * W), round(v2 * H)))
            # Vanilla tiles come out 62-64px because the UVs carry a gutter.
            # Normalise so every cell lands on the grid exactly.
            tiles[name] = crop.resize((TILE, TILE), Image.LANCZOS)
    return tiles


# ------------------------------------------------------------- output -----
def write_uvmap(placed: dict[str, tuple[int, int]]) -> None:
    rows = []
    for name, (cx, cy) in sorted(placed.items(), key=lambda kv: kv[1][1] * GRID + kv[1][0]):
        u1, v1 = cx / GRID, cy / GRID
        rows.append(
            f'                <node id="IconUV">\n'
            f'                    <attribute id="MapKey" type="FixedString" value="{name}"/>\n'
            f'                    <attribute id="U1" type="float" value="{u1:.8f}"/>\n'
            f'                    <attribute id="U2" type="float" value="{u1 + 1 / GRID:.8f}"/>\n'
            f'                    <attribute id="V1" type="float" value="{v1:.8f}"/>\n'
            f'                    <attribute id="V2" type="float" value="{v1 + 1 / GRID:.8f}"/>\n'
            f'                </node>')
    UVMAP.parent.mkdir(parents=True, exist_ok=True)
    UVMAP.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<save>\n'
        '    <version major="4" minor="2" revision="0" build="300"/>\n'
        '    <region id="TextureAtlasInfo">\n'
        '        <node id="root">\n'
        '            <children>\n'
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
        '            </children>\n'
        '        </node>\n'
        '    </region>\n'
        '    <region id="IconUVList">\n'
        '        <node id="root">\n'
        '            <children>\n'
        + "\n".join(rows) + "\n"
        '            </children>\n'
        '        </node>\n'
        '    </region>\n'
        '</save>\n', encoding="utf-8")


def write_texbank() -> None:
    """The TextureBank entry, as .lsf - the format the game loads.

    Shape copied from a working mod. Resource.ID must equal the UV map's
    TextureAtlasPath.UUID; that pairing is what links the two files.
    """
    lsx = OBJ / "_texbank.lsx"
    lsx.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<save>\n'
        '\t<version major="4" minor="0" revision="9" build="328"/>\n'
        '\t<region id="TextureBank">\n'
        '\t\t<node id="TextureBank">\n'
        '\t\t\t<children>\n'
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
        '\t\t\t\t</node>\n'
        '\t\t\t</children>\n'
        '\t\t</node>\n'
        '\t</region>\n'
        '</save>\n', encoding="utf-8")
    TEXBANK.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(DIVINE), "-g", "bg3", "-a", "convert-resource",
                        "-s", str(lsx), "-d", str(TEXBANK), "-o", "lsf"],
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"lsf conversion failed: {(r.stderr or r.stdout)[:300]}")
    # Delete the intermediate. Left behind, it is an .lsx carrying a live-looking
    # UUID that nothing ships - and validate.py's cross-mod GUID check duly
    # reported it as a dangling reference the moment the real atlas was removed.
    lsx.unlink(missing_ok=True)


def main() -> int:
    # GUARD, added 2026-08-20. tools/feature_icons.py now owns ATLAS_DDS, UVMAP
    # and TEXBANK - the same three paths this script writes, under the same
    # ATLAS_UUID. Running this would silently replace the shipping Warp Dice
    # atlas with the recoloured set the user reverted in v1.5.4, and every check
    # would stay green because the replacement is itself a valid atlas.
    #
    # This file is kept rather than deleted because the v1.5.4 note said to keep
    # it for whenever there is better art. --preview still works; it touches
    # nothing. To build from here again, merge the two tools so one process owns
    # the atlas - do not just delete this guard.
    if "--preview" not in sys.argv and "--names" not in sys.argv:
        print("REFUSING TO RUN: tools/feature_icons.py owns the atlas now.\n"
              "  It writes the same three files under the same UUID, and this\n"
              "  script would overwrite the shipping Warp Dice icon with the\n"
              "  recoloured set reverted in v1.5.4.\n"
              "  --preview and --names still work. To build from here, merge the\n"
              "  two tools first so one of them owns the atlas.", file=sys.stderr)
        return 2

    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true",
                    help="write obj/atlas/_compare.png and stop")
    ap.add_argument("--names", action="store_true", help="print the rename map and stop")
    args = ap.parse_args()

    if args.names:
        for k, v in RENAME.items():
            print(f"  {k:<36} -> {v}")
        return 0

    tiles = extract_tiles()
    missing = [k for k in RENAME if k not in tiles]
    if missing:
        print("could not find in any vanilla atlas: " + ", ".join(missing), file=sys.stderr)
        return 2
    print(f"extracted {len(tiles)} vanilla tiles")

    done = {name: recolour(img) for name, img in tiles.items()}

    if args.preview:
        cell = 150
        sheet = Image.new("RGBA", (len(done) * cell, 2 * cell), (24, 24, 28, 255))
        for i, name in enumerate(RENAME):
            sheet.alpha_composite(tiles[name].resize((cell, cell), Image.LANCZOS), (i * cell, 0))
            sheet.alpha_composite(done[name].resize((cell, cell), Image.LANCZOS), (i * cell, cell))
        out = OBJ / "_compare.png"
        sheet.save(out)
        print(f"preview -> {out}  (top vanilla, bottom recoloured)")
        return 0

    atlas = Image.new("RGBA", (ATLAS_PX, ATLAS_PX), (0, 0, 0, 0))
    placed: dict[str, tuple[int, int]] = {}
    for i, vanilla_name in enumerate(RENAME):
        cx, cy = i % GRID, i // GRID
        atlas.alpha_composite(done[vanilla_name], (cx * TILE, cy * TILE))
        placed[RENAME[vanilla_name]] = (cx, cy)

    OBJ.mkdir(parents=True, exist_ok=True)
    png = OBJ / "Icons_Warpblade.png"
    atlas.save(png)

    ATLAS_DDS.parent.mkdir(parents=True, exist_ok=True)
    # BC7_UNORM, never the _SRGB variant - the same rule tools/dds_format.py
    # established for the class icons, and what the working reference mod uses.
    r = subprocess.run([str(TEXCONV), "-f", "BC7_UNORM", "-y", "-m", "1",
                        "-o", str(ATLAS_DDS.parent), str(png)],
                       capture_output=True, text=True, errors="replace")
    # CASE MATTERS, AND Path.is_file() CANNOT SEE IT. texconv writes lowercase
    # ".dds"; the UV map and the TextureBank both reference uppercase ".DDS".
    # BG3 looks paths up inside the pak CASE-SENSITIVELY, but Windows' filesystem
    # is case-INSENSITIVE - so `(dir / "Icons_Warpblade.DDS").is_file()` returned
    # True for a file actually named ".dds", the rename below never ran, Divine
    # packed the real lowercase name, and every spell icon in the game went blank
    # while the loose class and resource icons were untouched. Shipped in v1.5.
    #
    # So: read the REAL name from the directory listing, and rename through a
    # temporary because Windows will not rename a file to a case-variant of
    # itself in one step.
    entries = {p.name for p in ATLAS_DDS.parent.iterdir() if p.is_file()}
    stem = ATLAS_DDS.stem
    actual = next((n for n in entries if n.lower() == f"{stem.lower()}.dds"), None)
    if actual is None:
        raise SystemExit(f"texconv produced nothing: {(r.stderr or r.stdout)[:300]}")
    if actual != ATLAS_DDS.name:
        tmp = ATLAS_DDS.parent / f"_{stem}.tmp"
        (ATLAS_DDS.parent / actual).replace(tmp)
        tmp.replace(ATLAS_DDS)
        entries = {p.name for p in ATLAS_DDS.parent.iterdir() if p.is_file()}
        if ATLAS_DDS.name not in entries:
            raise SystemExit(f"case fix failed - directory still holds {sorted(entries)}")
        print(f"  renamed {actual} -> {ATLAS_DDS.name} (case matters inside the pak)")
    print(f"atlas  -> {ATLAS_DDS.relative_to(MOD)}  "
          f"({ATLAS_PX}x{ATLAS_PX}, {len(placed)} of {GRID * GRID} slots used)")

    write_uvmap(placed)
    print(f"uv map -> {UVMAP.relative_to(MOD)}")
    write_texbank()
    print(f"texbank-> {TEXBANK.relative_to(MOD)}")

    print("\nIcon names to use in stats:")
    for k, v in RENAME.items():
        print(f"  {k:<36} -> {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
