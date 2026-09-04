# SPDX-License-Identifier: GPL-3.0-or-later
# forge.py - bootstrap a Baldur's Gate 3 class or subclass mod.
# Copyright (C) 2026 John Wilbur
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.

"""Class Forge - scaffold a BG3 class or subclass mod that LOADS before you write anything.

    py forge.py doctor                 # is this machine set up to build a mod at all
    py forge.py classes                # the real vanilla classes, read from YOUR game data
    py forge.py init                   # interview -> forge.json
    py forge.py init --answers a.json  # same, non-interactive
    py forge.py scaffold               # forge.json -> a complete, loadable mod tree

⭐ WHY THIS EXISTS RATHER THAN MORE INSTRUCTIONS
    This framework started as prose, and prose has a ceiling: however good the
    instructions, they are still instructions. A first-timer hand-writes LSX, mistypes a
    UUID, copies a GUID out of a tutorial, and the game dies at level-up with no message
    that means anything.

    So this generates a mod that already loads. Empty of design, complete of plumbing:
    identity, class description, a progression table, one placeholder feature, a
    spendable RESOURCE with a placeholder SPELL that costs it, a spell list, a levelmap,
    and the localisation to name them all. You get something in the game in the first
    session, and THEN you design.

⚠ THE RULE THIS TOOL ENFORCES, AND THE REASON IT CAN REFUSE TO RUN
    **It never invents a UUID and never ships one you found somewhere.**
    - Every UUID it generates is fresh (uuid4) and recorded in forge.json, so nothing is
      copied from a tutorial and nothing collides.
    - Every VANILLA UUID it references - the parent class you attach to - is READ OUT OF
      YOUR OWN UNPACKED GAME DATA. Not a table in this file, not memory. If the unpacked
      data is not there, `scaffold` REFUSES rather than guessing, because a plausible
      wrong GUID is the single most expensive thing this tool could hand you: it looks
      right, it validates, and it crashes at character creation.

    That refusal is the feature. Read `doctor` before complaining about it.

WHAT IT DELIBERATELY DOES NOT DO
    It does not decide WHAT THE CLASS IS. Every generated feature and spell is
    deliberately dull - correct plumbing, no design - because a generator that guessed at
    a concept would produce a class nobody chose. Scaffold a mechanically VALID spell,
    never a balanced one: whether it is worth its cost is a conversation with the agent,
    and a thing the balance tools price.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

# A console inherits whatever codepage it has (cp1252 in Git Bash here) and this
# module's docstring is UTF-8. Without this, `--help` dies on the first star -
# argparse prints the docstring as its description. Measured, not hypothetical.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
UNPACKED = Path(os.environ.get("BG3_UNPACKED", r"C:\Modding\bg3_unpacked"))
LOCALAPP = Path(os.environ.get("LOCALAPPDATA", ""))
MODS_DIR = LOCALAPP / "Larian Studios" / "Baldur's Gate 3" / "Mods"

SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,39}$")


class ForgeError(RuntimeError):
    """Refuse loudly. Never guess past a missing prerequisite."""


# ------------------------------------------------------------------ identity ---
def new_uuid() -> str:
    return str(uuid.uuid4())


def new_handle() -> str:
    """A localisation handle in the game's own shape: h + a uuid with 'g' separators.

    Vanilla writes e.g. h4f4dee77gb10dg43bcgacd9g082acf8795d1. Generating them rather
    than copying one matters more than it looks: a handle reused from another mod points
    at THAT mod's string, so your ability silently displays someone else's name.
    """
    return "h" + str(uuid.uuid4()).replace("-", "g")


def version64(major: int, minor: int, revision: int, build: int) -> int:
    """Larian's packed version. Getting the shifts wrong makes a mod manager show 0.0.0.0."""
    return (major << 55) | (minor << 47) | (revision << 31) | build


# -------------------------------------------------------------- game data ------
def unpacked_class_files(root: Path) -> list[Path]:
    return sorted(root.rglob("ClassDescriptions/ClassDescriptions.lsx"))


def read_classes(root: Path = UNPACKED) -> dict:
    """Real class UUIDs, parsed from the player's OWN unpacked game data.

    ⚠ Deliberately NOT a hardcoded table. A table in this file would be a list of GUIDs
    someone typed once, which is exactly the artefact this tool exists to stop people
    shipping - and it would rot the first time Larian added a class. Reading the game is
    both correct and self-updating.

    Returns {name: {"uuid", "progression_table", "parent"}}. A base class has no parent.
    """
    if not root.is_dir():
        raise ForgeError(
            f"no unpacked game data at {root}.\n"
            f"Unpack Shared.pak and Gustav.pak with Divine or BG3 Modders Multitool, or "
            f"set BG3_UNPACKED.\nThis tool will not guess a class GUID - see `doctor`.")
    files = unpacked_class_files(root)
    if not files:
        raise ForgeError(
            f"{root} exists but contains no ClassDescriptions.lsx. The unpack looks "
            f"partial -\nextract at least Shared.pak.")
    out: dict[str, dict] = {}
    for f in files:
        try:
            tree = ET.parse(f)
        except ET.ParseError as e:
            raise ForgeError(f"{f} is not well-formed XML: {e}") from e
        for node in tree.iter("node"):
            if node.get("id") != "ClassDescription":
                continue
            attrs = {a.get("id"): (a.get("value") or a.get("handle"))
                     for a in node.findall("attribute")}
            name = attrs.get("Name")
            if not name:
                continue
            out[name] = {"uuid": attrs.get("UUID", ""),
                         "progression_table": attrs.get("ProgressionTableUUID", ""),
                         "parent": attrs.get("ParentGuid", "") or ""}
    return out


def base_classes(classes: dict) -> dict:
    """Classes with no parent - the twelve you can hang a subclass off."""
    return {n: c for n, c in classes.items() if not c["parent"]}


# ------------------------------------------------------------------ doctor -----
def doctor(unpacked: Path) -> int:
    rows = []

    ok_unpack = unpacked.is_dir() and bool(unpacked_class_files(unpacked))
    rows.append(("unpacked game data", ok_unpack, str(unpacked),
                 "REQUIRED. Without it this tool refuses to scaffold, because it would "
                 "have to guess your parent class's GUID."))

    n = 0
    if ok_unpack:
        try:
            n = len(base_classes(read_classes(unpacked)))
        except ForgeError:
            n = 0
    rows.append(("vanilla classes readable", n >= 12, f"{n} base class(es) found",
                 "12 is vanilla. Fewer means a partial unpack; more means you have "
                 "class mods installed, which is fine."))

    divine = shutil.which("Divine") or shutil.which("divine.exe")
    rows.append(("Divine (packer)", bool(divine), divine or "not on PATH",
                 "Needed to build a .pak. Not needed to scaffold - you can generate the "
                 "tree now and sort packing out later."))

    rows.append(("BG3 Mods folder", MODS_DIR.is_dir(), str(MODS_DIR),
                 "Where a built pak is deployed. Launch the game once if it is missing."))

    print()
    hard_fail = False
    for label, ok, detail, note in rows:
        mark = "ok  " if ok else "MISS"
        print(f"  {mark}  {label:<26} {detail}")
        if not ok:
            print(f"        -> {note}")
            hard_fail = hard_fail or label.startswith("unpacked")
    print()
    if hard_fail:
        print("Not ready: the unpacked game data is the one true prerequisite.")
        return 1
    print("Ready to scaffold.")
    return 0


# ------------------------------------------------------------------ templates --
META = """<?xml version="1.0" encoding="utf-8"?>
<save>
    <version major="4" minor="0" revision="9" build="331"/>
    <region id="Config">
        <node id="root">
            <children>
                <node id="Dependencies"/>
                <node id="ModuleInfo">
                    <attribute id="Author" type="LSString" value="{{AUTHOR}}"/>
                    <attribute id="CharacterCreationLevelName" type="FixedString" value=""/>
                    <attribute id="Description" type="LSString" value="{{DESCRIPTION}}"/>
                    <attribute id="Folder" type="LSString" value="{{NAME}}"/>
                    <attribute id="LobbyLevelName" type="FixedString" value=""/>
                    <attribute id="MD5" type="LSString" value=""/>
                    <attribute id="MainMenuBackgroundVideo" type="FixedString" value=""/>
                    <attribute id="MenuLevelName" type="FixedString" value=""/>
                    <attribute id="Name" type="FixedString" value="{{NAME}}"/>
                    <attribute id="NumPlayers" type="uint8" value="4"/>
                    <attribute id="PhotoBooth" type="FixedString" value=""/>
                    <attribute id="StartupLevelName" type="FixedString" value=""/>
                    <attribute id="Tags" type="LSString" value="{{TAGS}}"/>
                    <attribute id="Type" type="FixedString" value="Add-on"/>
                    <attribute id="UUID" type="FixedString" value="{{MOD_UUID}}"/>
                    <attribute id="Version64" type="int64" value="{{VERSION64}}"/>
                    <children>
                        <node id="PublishVersion">
                            <attribute id="Version64" type="int64" value="{{VERSION64}}"/>
                        </node>
                        <node id="Scripts"/>
                        <node id="TargetModes">
                            <children>
                                <node id="Target">
                                    <attribute id="Object" type="FixedString" value="Story"/>
                                </node>
                            </children>
                        </node>
                    </children>
                </node>
            </children>
        </node>
    </region>
</save>
"""

CLASSDESC = """<?xml version="1.0" encoding="utf-8"?>
<save>
    <version major="4" minor="4" revision="0" build="444"/>
    <region id="ClassDescriptions">
        <node id="root">
            <children>
                <!-- {{NAME}} - a {{PARENT}} subclass.
                     ParentGuid was READ FROM THE UNPACKED GAME DATA on this machine, not
                     copied from a guide. Every other UUID here was generated fresh.
                     Do NOT add a copy of the {{PARENT}} node itself. Nothing in a
                     ClassDescription references its subclasses, so there is nothing to
                     override, and overriding it is a liability on every game patch. -->
                <node id="ClassDescription">
                    <attribute id="CharacterCreationPose" type="guid" value="0f07ec6e-4ef0-434e-9a51-1353260ccff8"/>
                    <attribute id="Description" type="TranslatedString" handle="{{H_DESC}}" version="1"/>
                    <attribute id="DisplayName" type="TranslatedString" handle="{{H_NAME}}" version="1"/>
                    <attribute id="ShortName" type="TranslatedString" handle="{{H_SHORT}}" version="1"/>
                    <attribute id="LearningStrategy" type="uint8" value="1"/>
                    <attribute id="MustPrepareSpells" type="bool" value="false"/>
                    <attribute id="CanLearnSpells" type="bool" value="false"/>
                    <attribute id="Name" type="FixedString" value="{{NAME}}"/>
                    <attribute id="ParentGuid" type="guid" value="{{PARENT_UUID}}"/>
                    <attribute id="PrimaryAbility" type="uint8" value="{{PRIMARY_ABILITY}}"/>
                    <attribute id="ProgressionTableUUID" type="guid" value="{{TABLE_UUID}}"/>
                    <attribute id="SoundClassType" type="FixedString" value="{{PARENT}}"/>
                    <attribute id="SpellCastingAbility" type="uint8" value="0"/>
                    <attribute id="UUID" type="guid" value="{{CLASS_UUID}}"/>
                </node>
            </children>
        </node>
    </region>
</save>
"""

PROGRESSIONS = """<?xml version="1.0" encoding="utf-8"?>
<save>
    <version major="4" minor="0" revision="4" build="444"/>
    <region id="Progressions">
        <node id="root">
            <children>
                <!-- Every row shares TableUUID {{TABLE_UUID}}, which ClassDescriptions
                     names as ProgressionTableUUID. If those two ever disagree the
                     subclass silently fails to appear, with no error anywhere.
                     ProgressionType 1 = Subclass.

                     There is deliberately NO copy of the base-game {{PARENT}} level-3
                     node here. Registration is handled at runtime from the ParentGuid
                     alone, so this mod never overrides vanilla data and cannot break it.

                     ⚠ ADD A ROW PER LEVEL YOUR SUBCLASS GRANTS AT. Naming a passive in
                     PassivesAdded that does not exist crashes at LEVEL-UP - after the
                     player has already committed to the choice. Your validator should
                     check every name here against your stats files. -->
                <node id="Progression">
                    <attribute id="Level" type="uint8" value="{{FIRST_LEVEL}}"/>
                    <attribute id="Name" type="LSString" value="{{NAME}}"/>
                    <attribute id="PassivesAdded" type="LSString" value="{{FEATURE_ID}}"/>
                    <!-- Grants the pool. Without this row the resource EXISTS and the
                         player has none of it, so every spell that costs it is greyed
                         out with no explanation. -->
                    <attribute id="Boosts" type="LSString" value="ActionResource({{RESOURCE_ID}},2,0)"/>
                    <!-- Grants the spell list. The UUID is the SpellList's, not a
                         spell's - `AddSpells(<uuid>)` is the form vanilla uses. -->
                    <attribute id="Selectors" type="LSString" value="AddSpells({{SPELLLIST_UUID}})"/>
                    <attribute id="ProgressionType" type="uint8" value="1"/>
                    <attribute id="TableUUID" type="guid" value="{{TABLE_UUID}}"/>
                    <attribute id="UUID" type="guid" value="{{PROG_UUID}}"/>
                </node>
            </children>
        </node>
    </region>
</save>
"""

PROGDESC = """<?xml version="1.0" encoding="utf-8"?>
<save>
    <version major="4" minor="0" revision="4" build="444"/>
    <region id="ProgressionDescriptions">
        <node id="root">
            <children>
                <node id="ProgressionDescription">
                    <attribute id="DisplayName" type="TranslatedString" handle="{{H_FEAT_NAME}}" version="1"/>
                    <attribute id="Description" type="TranslatedString" handle="{{H_FEAT_DESC}}" version="1"/>
                    <attribute id="ProgressionId" type="guid" value="{{PROG_UUID}}"/>
                    <attribute id="ProgressionTableId" type="guid" value="{{TABLE_UUID}}"/>
                    <attribute id="UUID" type="guid" value="{{PROGDESC_UUID}}"/>
                </node>
            </children>
        </node>
    </region>
</save>
"""

PASSIVE = """// {{NAME}} - generated by forge.py. One placeholder feature, so the whole chain is
// provable in game before any design exists: progression -> passive -> localisation.
//
// ⚠ REPLACE THIS, do not build on it. It is deliberately inert - a Boost that does
// nothing controversial - so that if it does NOT show up in game, the problem is your
// plumbing and not your design.

new entry "{{FEATURE_ID}}"
type "PassiveData"
data "DisplayName" "{{H_FEAT_NAME}};1"
data "Description" "{{H_FEAT_DESC}};1"
data "Properties" "Highlighted"
data "Boosts" "Advantage(SavingThrow,Charisma)"
"""

LOCA = """<?xml version="1.0" encoding="utf-8"?>
<contentList>
  <content contentuid="{{H_NAME}}" version="1">{{NAME}}</content>
  <content contentuid="{{H_SHORT}}" version="1">{{NAME}}</content>
  <content contentuid="{{H_DESC}}" version="1">{{DESCRIPTION}}</content>
  <content contentuid="{{H_FEAT_NAME}}" version="1">{{FEATURE_NAME}}</content>
  <content contentuid="{{H_FEAT_DESC}}" version="1">Placeholder. Replace this description once the feature does something.</content>
  <content contentuid="{{H_RES_NAME}}" version="1">{{RESOURCE_NAME}}</content>
  <content contentuid="{{H_RES_DESC}}" version="1">A pool of {{RESOURCE_NAME}}, spent by {{NAME}} abilities and restored on a short rest.</content>
  <content contentuid="{{H_SPELL_NAME}}" version="1">{{SPELL_NAME}}</content>
  <content contentuid="{{H_SPELL_DESC}}" version="1">Placeholder. A weapon attack that spends one {{RESOURCE_NAME}}. Replace this once the spell does something worth describing.</content>
</contentList>
"""

# ⭐ EVERYTHING BELOW WAS READ OUT OF SHIPPED GAME DATA, NOT REMEMBERED.
# The SpellList node's three attributes and the `AddSpells(<uuid>)` selector form were
# both taken from GustavX's own Lists/SpellLists.lsx and Progressions.lsx. That matters
# because a scaffold that emits a plausible-but-wrong shape is worse than none: it looks
# authoritative and fails silently.

ACTIONRESOURCE = """<?xml version="1.0" encoding="UTF-8"?>
<save>
    <version major="4" minor="3" revision="0" build="333"/>
    <region id="ActionResourceDefinitions">
        <node id="root">
            <children>
                <!-- {{RESOURCE_NAME}} - the pool your class spends.
                     Granted in Progressions.lsx as: ActionResource({{RESOURCE_ID}},n,0)
                     Spent in SpellData as:          UseCosts "{{RESOURCE_ID}}:1"

                     ⚠ ShowOnActionResourcePanel MUST be true or the player never sees
                     the pool, and a resource nobody can see reads as a broken spell. -->
                <node id="ActionResourceDefinition">
                    <attribute id="Description"               type="TranslatedString" handle="{{H_RES_DESC}}" version="1"/>
                    <attribute id="DisplayName"               type="TranslatedString" handle="{{H_RES_NAME}}" version="1"/>
                    <attribute id="MaxLevel"                  type="uint32"      value="0"/>
                    <attribute id="Name"                      type="FixedString" value="{{RESOURCE_ID}}"/>
                    <!-- ShortRest replenishes on long rest too. LongRest does not. -->
                    <attribute id="ReplenishType"             type="FixedString" value="ShortRest"/>
                    <attribute id="IsSpellResource"           type="bool"        value="false"/>
                    <attribute id="ShowOnActionResourcePanel" type="bool"        value="true"/>
                    <attribute id="UUID"                      type="guid"        value="{{RESOURCE_UUID}}"/>
                </node>
            </children>
        </node>
    </region>
</save>
"""

LEVELMAP = """<?xml version="1.0" encoding="utf-8"?>
<save>
    <version major="4" minor="0" revision="9" build="331"/>
    <region id="LevelMapValues">
        <node id="root">
            <children>
                <!-- A value that scales with class level, referenced from a stats field
                     as LevelMapValue({{LEVELMAP_ID}}).

                     ⚠ THIS IS THE ONLY WAY TO SCALE A NUMBER BY LEVEL. A passive cannot
                     change a die size or a magnitude that lives in a damage expression;
                     it can only add or remove itself. Vanilla scales SuperiorityDie,
                     sneak attack and cantrip damage exactly this way.

                     Add a <node id="LevelMapValue"> per breakpoint. FallbackValue is
                     what applies below the lowest level named. -->
                <node id="LevelMap">
                    <attribute id="FallbackValue" type="LSString"  value="1d8"/>
                    <attribute id="Level{{FIRST_LEVEL}}" type="LSString" value="1d8"/>
                    <attribute id="Name"          type="FixedString" value="{{LEVELMAP_ID}}"/>
                    <attribute id="PreferredClassUUID" type="guid"  value="{{PARENT_UUID}}"/>
                    <attribute id="UUID"          type="guid"       value="{{LEVELMAP_UUID}}"/>
                </node>
            </children>
        </node>
    </region>
</save>
"""

SPELLLIST = """<?xml version="1.0" encoding="UTF-8"?>
<save>
    <version major="4" minor="8" revision="0" build="0"/>
    <region id="SpellLists">
        <node id="root">
            <children>
                <!-- Progressions.lsx grants this whole list with
                     AddSpells({{SPELLLIST_UUID}}). Add more spell names to `Spells`,
                     semicolon-separated, and they are granted together. -->
                <node id="SpellList">
                    <attribute id="Name"   type="FixedString" value="{{NAME}} Spells"/>
                    <attribute id="Spells" type="LSString"    value="{{SPELL_ID}}"/>
                    <attribute id="UUID"   type="guid"        value="{{SPELLLIST_UUID}}"/>
                </node>
            </children>
        </node>
    </region>
</save>
"""

SPELL = """// {{NAME}} - spells.
//
// ⚠ REPLACE THIS. It is a deliberately dull weapon attack that costs one
// {{RESOURCE_ID}}, present so the whole chain is provable in game before any design
// exists: resource -> spell -> spell list -> progression -> hotbar.
//
// Three things vanilla does that are easy to get wrong, and all three are here:
//
//   1. ExecuteWeaponFunctors(MainHand) is what makes weapon enchantments and on-hit
//      riders fire. Omit it and the player's magic sword silently stops working
//      through your spell.
//   2. DealDamage needs the damage TYPE, not just the amount.
//   3. HitCosts vs UseCosts. HitCosts spends only on a hit, which is how Battle
//      Master maneuvers work; UseCosts spends on a miss too. Choose deliberately -
//      it is one of the largest balance levers you have.

new entry "{{SPELL_ID}}"
type "SpellData"
data "SpellType" "Target"
data "TargetRadius" "MeleeMainWeaponRange"
data "TargetConditions" "Character() and not Self()"
data "Icon" "Action_PushingAttack_Melee"     // a REAL vanilla icon, not an invented name
data "DisplayName" "{{H_SPELL_NAME}};1"
data "Description" "{{H_SPELL_DESC}};1"
data "UseCosts" "ActionPoint:1"
data "HitCosts" "{{RESOURCE_ID}}:1"
data "SpellSuccess" "ExecuteWeaponFunctors(MainHand);DealDamage(max(1,MainMeleeWeapon),MainMeleeWeaponDamageType)"
data "SpellRoll" "Attack(AttackType.MeleeWeaponAttack)"
data "SpellProperties" "GROUND:SurfaceChange(Douse)"
data "VerbalIntent" "Damage"
data "SpellFlags" "IsMelee;IsHarmful;IsSpell"
data "SpellAnimation" "b3e6f0f1-4d5f-4ba2-a1b7-56b1a3e0d81a,,;,,;dc2e5bd1-7d64-4b9c-9b6f-9c1c0e5b6c2f,,;,,;,,;,,;,,;,,;,,"
"""

# ------------------------------------------------------------- class icon -----
# Sizes and paths measured from a working mod's own GUI tree, not guessed:
#   Assets/ClassIcons/<Name>.DDS               300x300
#   Assets/ClassIcons/hotbar/<Name>.DDS        140x140
#   AssetsLowRes/ClassIcons/<Name>.DDS         152x152
#   AssetsLowRes/ClassIcons/hotbar/<Name>.DDS   72x72
ICON_SIZES = [
    ("Assets/ClassIcons/{name}.DDS", 300),
    ("Assets/ClassIcons/hotbar/{name}.DDS", 140),
    ("AssetsLowRes/ClassIcons/{name}.DDS", 152),
    ("AssetsLowRes/ClassIcons/hotbar/{name}.DDS", 72),
]


def icon_colour(name: str) -> tuple:
    """A stable colour per class name. Deterministic so a re-scaffold does not reshuffle."""
    h = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:6], 16)
    # Keep it dark and saturated - BG3's character sheet puts these on a light panel,
    # and a pale placeholder is invisible rather than obviously temporary.
    return (60 + (h & 0x3F), 40 + ((h >> 8) & 0x3F), 90 + ((h >> 16) & 0x5F), 255)


def write_class_icon(root: Path, name: str) -> list[str]:
    """Write a rudimentary placeholder class icon in all four sizes.

    ⭐ WHY A GENERATOR SHIPS ART AT ALL, WHEN IT REFUSES TO SHIP DESIGN.
        A missing class icon is a BLANK ENTRY on the character-creation screen. That is a
        plumbing failure wearing an art failure's clothes: the mod is not broken-looking,
        it is invisible, and a first-timer reads that as "my subclass did not register"
        and starts debugging the wrong thing.

        So this writes something deliberately crude - a disc and a letter - whose only
        job is to prove the slot is wired. Replace it. It is meant to look temporary.

    ⚠ UNVERIFIED IN GAME. These are UNCOMPRESSED RGBA DDS written by Pillow, not BC-
        compressed via texconv the way a shipping mod would be. The format is standard and
        the game should read it, but nobody has loaded one of these into BG3 yet. If the
        slot is still blank, run the .png beside it through texconv and use that.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return []

    made = []
    base = 300
    img = Image.new("RGBA", (base, base), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = icon_colour(name)
    d.ellipse((10, 10, base - 10, base - 10), fill=col,
              outline=(230, 220, 190, 255), width=6)
    letter = name[0].upper()
    # No font file is guaranteed on any machine, so the default bitmap font is used and
    # scaled up. It looks crude, which is the point.
    tmp = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((22, 22), letter, fill=(240, 235, 215, 255), anchor="mm")
    img.alpha_composite(tmp.resize((base // 2, base // 2), Image.LANCZOS),
                        (base // 4, base // 4))

    for rel, size in ICON_SIZES:
        p = root / "Mods" / name / "GUI" / rel.format(name=name)
        p.parent.mkdir(parents=True, exist_ok=True)
        resized = img.resize((size, size), Image.LANCZOS)
        resized.save(p)
        # The .png beside it is the thing you hand to texconv, or edit.
        resized.save(p.with_suffix(".png"))
        made.append(str(p.relative_to(root)).replace("\\", "/"))
    return made


# ---------------------------------------------------- GUI texture metadata -----
# ⭐ FOUND IN GAME, 2026-09-02. Shipping the four .DDS files is NOT enough. BG3 refuses
# them with "missing texture metadata" naming `Mods/<Mod>/GUI` unless this file exists.
# The scaffold shipped icons that could not load, and no static check caught it, because
# nothing was malformed - a file was simply absent.
#
# The format was read out of a mod that works in game, not out of documentation:
#   - one entry per texture, MapKey is the **.png** path relative to Mods/<Mod>/GUI/,
#     NOT the .DDS path,
#   - w / h / mipcount per entry,
#   - **hi-res only.** AssetsLowRes entries are deliberately absent, which is the same
#     rule PART 3.5 states from the other direction.
GUI_METADATA_LSX = """<?xml version="1.0" encoding="utf-8"?>
<save>
	<version major="4" minor="8" revision="0" build="500" lslib_meta="v1,bswap_guids" />
	<region id="config">
		<node id="config">
			<children>
				<node id="entries">
					<children>
{{ENTRIES}}					</children>
				</node>
			</children>
		</node>
	</region>
</save>
"""

GUI_METADATA_ENTRY = """						<node id="Object">
							<attribute id="MapKey" type="FixedString" value="{rel}" />
							<children>
								<node id="entries">
									<attribute id="h" type="int16" value="{size}" />
									<attribute id="mipcount" type="int8" value="1" />
									<attribute id="w" type="int16" value="{size}" />
								</node>
							</children>
						</node>
"""


def write_gui_metadata(root: Path, name: str) -> list[str]:
    """Write Mods/<name>/GUI/metadata.lsf, converting via Divine when it is present.

    Without Divine the .lsx is left in place and the exact conversion command is
    printed. That is a real, recoverable state - NOT a silent one, because an icon
    with no metadata entry is the failure this function exists to prevent.
    """
    entries = "".join(
        GUI_METADATA_ENTRY.format(rel=rel.format(name=name).replace(".DDS", ".png"),
                                  size=size)
        for rel, size in ICON_SIZES
        if not rel.startswith("AssetsLowRes"))          # hi-res only. See the note above.

    gui = root / "Mods" / name / "GUI"
    gui.mkdir(parents=True, exist_ok=True)
    lsx = gui / "metadata.lsx"
    lsx.write_text(GUI_METADATA_LSX.replace("{{ENTRIES}}", entries), encoding="utf-8")

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import divine
        exe = divine.find_divine(probe=False)
    except Exception:
        exe = None

    if not exe:
        print("  ! Divine not found - Mods/%s/GUI/metadata.lsx was written but NOT"
              " converted." % name)
        print("    The game needs the .lsf. Run:")
        print("      divine.exe -g bg3 -a convert-resource -o lsf")
        print("        -s Mods/%s/GUI/metadata.lsx -d Mods/%s/GUI/metadata.lsf"
              % (name, name))
        return [str(lsx.relative_to(root)).replace("\\", "/")]

    lsf = gui / "metadata.lsf"
    # ⚠ Divine REFUSES a relative path ("Cannot proceed without absolute path [E2]")
    # and prints that refusal on STDOUT, leaving stderr empty. An error handler that
    # reads only stderr reports a blank reason for a perfectly clear failure.
    r = subprocess.run([str(exe), "-g", "bg3", "-a", "convert-resource", "-o", "lsf",
                        "-s", str(lsx.resolve()), "-d", str(lsf.resolve())],
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not lsf.is_file():
        why = ((r.stdout or "") + (r.stderr or "")).strip()[:200] or "no output"
        print("  ! Divine could not convert metadata.lsx: %s" % why)
        return [str(lsx.relative_to(root)).replace("\\", "/")]

    lsx.unlink()                    # the .lsf is the artifact; the .lsx was scaffolding
    return [str(lsf.relative_to(root)).replace("\\", "/")]


DESIGN_MD = """# {{NAME}}

*Generated by the Class Forge. This is the start of a design document, not a design.*

**A {{PARENT}} subclass, taken at level {{FIRST_LEVEL}}.**

{{DESCRIPTION}}

## What was decided at init

| | |
|---|---|
| Resource | **{{RESOURCE_NAME}}** (`{{RESOURCE_ID}}`), short-rest |
| Roughly as strong as | {{COMPARE_TO}} |
| Pool size | {{POOL_SIZE}} |
| Uses per turn | {{USES_PER_TURN}} |
| Designed for levels | {{LEVEL_RANGE}} |

**The comparison target is the load-bearing one.** "Is this too strong?" has no answer in
the abstract and a fairly easy one against a named baseline: price your feature against
{{COMPARE_TO}} at the same level and see which player is happier.

## What to decide next

1. **What does spending the resource actually DO?** One sentence. If it takes a paragraph,
   the class has two ideas in it and one of them belongs in a different mod.
2. **What is the cost that makes it interesting?** A resource nobody minds spending is not
   a resource, it is a cooldown with extra steps.
3. **What happens at the level cap** that the level-{{FIRST_LEVEL}} version cannot do?
4. **What does this class do BADLY?** A class with no weakness has no shape.

## Rules worth keeping in view

- **Verified means played in game**, never "validated clean". Static checks catch the
  plumbing; they cannot see a feature that fires correctly and pays the wrong person.
- **Measure before you design.** The shipped game data is right there - if you are about
  to guess at how vanilla does something, do not.
- Scaling by level lives in a **levelmap**, never in a passive.
"""


# r-string, and it is not style. This template contains Windows paths, and in a normal
# triple-quoted string Python reads a backslash-b escape as a BACKSPACE - so the
# generated shim looked for "forge<0x08>uild.ps1" and PowerShell said "illegal characters
# in path". Same failure as the heredoc-eaten escapes found earlier today, one layer in:
# the file on disk is CORRECT, so the control-character scanner cannot see it. Any
# template holding a backslash must be raw.
BUILD_SHIM = r"""<#
    Shim. The real build script is the Class Forge's build.ps1.

    It lives there so there is ONE build script for every mod made with this toolchain.
    A copy in each project would freeze: it stops receiving fixes the moment it is made.

        .\build.ps1              # validate, compile loca, pack, deploy
        .\build.ps1 -SkipPack    # checks only
#>
[CmdletBinding()]
param(
    [switch]$SkipPack,
    [switch]$SkipDeploy,
    [switch]$SkipValidate,
    [switch]$SkipSelfTest
)

$ErrorActionPreference = 'Stop'

# Walk up looking for forge\build.ps1 rather than assuming a fixed depth.
$dir = $PSScriptRoot
$real = $null
while ($dir -and -not $real) {
    $candidate = Join-Path $dir 'forge\build.ps1'
    if (Test-Path $candidate) { $real = $candidate; break }
    $parent = Split-Path $dir -Parent
    if ($parent -eq $dir) { break }
    $dir = $parent
}

if (-not $real) {
    throw "cannot find forge\build.ps1 at or above $PSScriptRoot. Keep this mod beside the Class Forge folder, or copy forge\build.ps1 here and edit this shim away."
}

& $real -Workspace $PSScriptRoot -SkipPack:$SkipPack -SkipDeploy:$SkipDeploy `
        -SkipValidate:$SkipValidate -SkipSelfTest:$SkipSelfTest
exit $LASTEXITCODE
"""


STARTER_VALIDATE = r'''"""Starter validator for {{NAME}} - generated by forge.py.

WHY THIS FILE EXISTS. An outside review of this framework (2026-08-25) found that
it "documents a strong validation regime, but the generated mod does not actually
receive a validator": build.ps1 called validation a gate, then printed
"validate.py not found - skipping" and packed anyway. A gate that is optional in
practice is not a gate, so every scaffolded mod now starts with this.

WHAT IT IS, HONESTLY. This checks LOCAL INVARIANTS ONLY - that the mod is
internally consistent. It CANNOT tell you a functor is real, that a status exists
in the game, or that a field is one the engine reads, because it does not look at
shipped game data. Those bugs cost a launch and a reroll to find, and the full
corpus-backed validator in a mature project catches them. Treat a clean run here
as "nothing is obviously self-contradictory", never as "this works".

    py tools/validate.py          # exit 1 on any ERROR
    py tools/validate.py --warn   # treat WARNs as failures too

Checks: XML parses; localisation handles declared; no duplicate UUIDs;
ClassDescription progression UUID matches every progression table; progression
passives exist; AddSpells targets a real spell list; spell-list entries exist;
spell resource costs resolve; spent resources are granted; `using` chains
terminate.
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ERR: list[str] = []
WARN: list[str] = []

# A floor, not a corpus. These are the vanilla resources a starter class is most
# likely to spend; anything else must be declared in ActionResourceDefinitions.lsx.
# If a real vanilla resource is missing here, add it - do not silence the check.
VANILLA_RESOURCES = {
    "ActionPoint", "BonusActionPoint", "ReactionActionPoint", "Movement",
    "SpellSlot", "KiPoint", "SuperiorityDie", "Rage", "SorceryPoint",
    "WildShape", "ChannelDivinity", "ChannelOath", "BardicInspiration",
    "LayOnHandsCharge", "ArcaneRecoveryPoint", "NaturalRecoveryPoint",
    "FungalInfestationCharge", "WarPriestActionPoint", "SneakAttack_Charge",
}


def err(msg: str) -> None:
    ERR.append(msg)


def warn(msg: str) -> None:
    WARN.append(msg)


def cfg() -> dict:
    f = ROOT / "forge.json"
    if not f.exists():
        err("forge.json missing - nothing can be located without it")
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def lsx_files() -> list[Path]:
    return sorted(ROOT.glob("Public/**/*.lsx")) + sorted(ROOT.glob("Mods/**/*.lsx"))


def stats_files() -> list[Path]:
    return sorted(ROOT.glob("Public/**/Stats/Generated/Data/*.txt"))


def parse_all() -> dict:
    trees = {}
    for f in lsx_files() + sorted(ROOT.glob("Localization/**/*.xml")):
        try:
            trees[f] = ET.parse(f)
        except ET.ParseError as e:
            err("{}: XML does not parse - {}".format(f.relative_to(ROOT), e))
    return trees


def attrs(tree, id_name):
    """Every <attribute id="..."> value in a parsed lsx."""
    return [a.get("value") for a in tree.iter("attribute") if a.get("id") == id_name]


def check_handles(trees) -> None:
    declared = set()
    for f, t in trees.items():
        if f.suffix == ".xml":
            declared.update(c.get("contentuid") for c in t.iter("content"))
    used = set()
    for f, t in trees.items():
        if f.suffix == ".lsx":
            for a in t.iter("attribute"):
                v = a.get("value") or ""
                if re.fullmatch(r"h[0-9a-f]{8,}\w*", v):
                    used.add(v)
    for f in stats_files():
        used.update(re.findall(r"\b(h[0-9a-f]{8}\w*)\b", f.read_text(encoding="utf-8")))
    for h in sorted(used - declared):
        err("localisation handle {} used but never declared".format(h))
    for h in sorted(declared - used):
        warn("localisation handle {} declared but never used".format(h))


def check_uuids(trees) -> None:
    seen = Counter()
    for f, t in trees.items():
        if f.suffix != ".lsx":
            continue
        for a in t.iter("attribute"):
            if (a.get("type") or "") == "guid" or a.get("id", "").endswith("UUID"):
                v = a.get("value") or ""
                if re.fullmatch(r"[0-9a-fA-F-]{36}", v):
                    seen[v] += 1
    # A UUID legitimately repeats when one record points at another, so only a
    # DEFINING collision matters: the same uuid as the identity of two records.
    ids = Counter()
    for f, t in trees.items():
        if f.suffix != ".lsx":
            continue
        for node in t.iter("node"):
            for a in node.findall("attribute"):
                if a.get("id") in ("UUID", "MapKey"):
                    ids[a.get("value")] += 1
    for u, n in ids.items():
        if n > 1 and u:
            err("UUID {} defines {} different records - references break unpredictably"
                .format(u, n))


def check_progressions(trees) -> None:
    table_ids, class_tables = set(), set()
    for f, t in trees.items():
        if f.suffix != ".lsx":
            continue
        if f.name == "ClassDescriptions.lsx":
            class_tables.update(v for v in attrs(t, "ProgressionTableUUID") if v)
        if f.name == "Progressions.lsx":
            table_ids.update(v for v in attrs(t, "TableUUID") if v)
    if class_tables and table_ids:
        for c in sorted(class_tables - table_ids):
            err("ClassDescriptions ProgressionTableUUID {} has no Progressions TableUUID - "
                "the subclass will appear and grant nothing".format(c))
        for p in sorted(table_ids - class_tables):
            warn("Progressions TableUUID {} is not referenced by any ClassDescription".format(p))
    elif not class_tables:
        err("no ProgressionTableUUID found in ClassDescriptions.lsx")


def check_passives_and_spells(trees) -> None:
    stats_text = {f: f.read_text(encoding="utf-8") for f in stats_files()}
    defined = set()
    for txt in stats_text.values():
        defined.update(re.findall(r'^new entry "([^"]+)"', txt, re.M))

    granted, spell_list_refs, costs, resource_grants = set(), set(), set(), set()
    for f, t in trees.items():
        if f.suffix != ".lsx" or f.name != "Progressions.lsx":
            continue
        for a in t.iter("attribute"):
            v = a.get("value") or ""
            if a.get("id") == "PassivesAdded":
                granted.update(x.strip() for x in v.split(";") if x.strip())
            if a.get("id") == "Selectors":
                spell_list_refs.update(re.findall(r"AddSpells\(([0-9a-fA-F-]{36})", v))
            if a.get("id") == "Boosts":
                resource_grants.update(re.findall(r"ActionResource\(([A-Za-z_]+)", v))

    for p in sorted(granted - defined):
        err('progression grants passive "{}" which no Passive.txt defines'.format(p))

    list_uuids, list_spells = set(), set()
    for f, t in trees.items():
        if f.suffix == ".lsx" and f.name == "SpellLists.lsx":
            list_uuids.update(v for v in attrs(t, "UUID") if v)
            for v in attrs(t, "Spells"):
                list_spells.update(x.strip() for x in (v or "").split(",") if x.strip())
    for u in sorted(spell_list_refs - list_uuids):
        err("AddSpells({}) references a spell list this mod does not define".format(u))
    for s in sorted(list_spells - defined):
        err('spell list contains "{}" which no stats file defines'.format(s))

    declared_res = set()
    for f, t in trees.items():
        if f.suffix == ".lsx" and "ActionResourceDefinitions" in f.name:
            declared_res.update(v for v in attrs(t, "Name") if v)
    for txt in stats_text.values():
        for m in re.findall(r'^data "UseCosts" "([^"]*)"', txt, re.M):
            for part in m.split(";"):
                name = part.split(":")[0].strip()
                if name and not name[0].isdigit():
                    costs.add(name)
    for c in sorted(costs):
        if c not in declared_res and c not in VANILLA_RESOURCES:
            err('spell costs "{}" which is neither a vanilla resource nor declared in '
                "ActionResourceDefinitions.lsx".format(c))
    for c in sorted(costs & declared_res):
        if c not in resource_grants:
            err('"{}" is spent by a spell and defined by this mod, but no progression '
                "grants it - the player can never pay the cost".format(c))


def check_using(trees) -> None:
    parents, entries = {}, set()
    for f in stats_files():
        txt = f.read_text(encoding="utf-8")
        cur = None
        for line in txt.splitlines():
            m = re.match(r'^new entry "([^"]+)"', line)
            if m:
                cur = m.group(1)
                entries.add(cur)
            m = re.match(r'^using "([^"]+)"', line)
            if m and cur:
                parents[cur] = m.group(1)
    for start in list(parents):
        seen, node = set(), start
        while node in parents:
            if node in seen:
                err('`using` chain starting at "{}" is a cycle'.format(start))
                break
            seen.add(node)
            node = parents[node]


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    strict = "--warn" in sys.argv
    name = cfg().get("name", "?")
    trees = parse_all()
    if not ERR:
        check_handles(trees)
        check_uuids(trees)
        check_progressions(trees)
        check_passives_and_spells(trees)
        check_using(trees)

    print("validate - {} (starter: LOCAL invariants only, not game data)".format(name))
    for e in ERR:
        print("  ERROR  " + e)
    for w in WARN:
        print("  warn   " + w)
    print("\n{} error(s), {} warning(s)".format(len(ERR), len(WARN)))
    if ERR or (strict and WARN):
        return 1
    print("no self-contradictions found. This is NOT proof the mod works in game.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

FILES = [
    ("Mods/{{NAME}}/meta.lsx", META),
    ("Public/{{NAME}}/ClassDescriptions/ClassDescriptions.lsx", CLASSDESC),
    ("Public/{{NAME}}/Progressions/Progressions.lsx", PROGRESSIONS),
    ("Public/{{NAME}}/Progressions/ProgressionDescriptions.lsx", PROGDESC),
    ("Public/{{NAME}}/Stats/Generated/Data/Passive.txt", PASSIVE),
    ("Public/{{NAME}}/Stats/Generated/Data/Spell_Target.txt", SPELL),
    ("Public/{{NAME}}/ActionResourceDefinitions/ActionResourceDefinitions.lsx", ACTIONRESOURCE),
    ("Public/{{NAME}}/Levelmaps/LevelMapValues.lsx", LEVELMAP),
    ("Public/{{NAME}}/Lists/SpellLists.lsx", SPELLLIST),
    ("Localization/English/{{NAME}}.xml", LOCA),
    ("DESIGN.md", DESIGN_MD),
    ("build.ps1", BUILD_SHIM),
    # ⚠ The one file here that is a TOOL rather than game data, and it is deliberate.
    # build.ps1 treats a missing tools/validate.py as a hard failure, so a scaffold
    # without this would refuse to build. It is a STARTER on purpose: local invariants
    # only, no corpus dependency. A mature project replaces it with the shared
    # corpus-backed validator; the header says so rather than letting someone mistake
    # a clean run for proof the mod works.
    ("tools/validate.py", STARTER_VALIDATE),
]


def fill(text: str, cfg: dict) -> str:
    out = text
    # The balance answers are nested under "balance" so forge.json stays readable, but
    # templates reference them flat as {{COMPARE_TO}} etc. Flattening here beats
    # duplicating them at the top level, where the two copies would drift.
    flat = {**cfg, **(cfg.get("balance") or {})}
    for k, v in flat.items():
        out = out.replace("{{" + k.upper() + "}}", str(v))
    left = re.findall(r"\{\{([A-Z_]+)\}\}", out)
    if left:
        raise ForgeError(f"template placeholder(s) never filled: {sorted(set(left))}. "
                         f"That would ship a literal {{{{...}}}} into a game file.")
    return out


# -------------------------------------------------------------------- init -----
QUESTIONS = [
    ("name", "Mod / subclass name (letters, digits, underscore; this becomes the folder "
             "and every EditorID prefix)", None),
    ("parent", "Parent class to attach to (see `py forge.py classes`)", "Fighter"),
    ("author", "Author name as it should appear on the mod page", None),
    ("description", "One sentence describing the subclass", None),
    ("feature_name", "Display name of your first feature (a placeholder is fine)",
     "Placeholder Feature"),
    ("resource_name", "Display name of the pool your class spends (e.g. Warp Dice)",
     "Focus"),
    ("spell_name", "Display name of your first spell/ability (a placeholder is fine)",
     "Placeholder Strike"),
    # ⭐ THE FOUR BALANCE QUESTIONS. Nothing below is needed to GENERATE a file - they are
    # here because they are the entire input to a balance model, and asking them at init
    # is the difference between a simulator you can run on day one and one retrofitted
    # after the class is already built and hard to change.
    ("compare_to", "What existing feature is yours the rough equivalent of? "
                   "(e.g. Battle Master's Superiority Dice)", "Superiority Dice"),
    ("pool_size", "How many of the resource at first level, and at the cap? (e.g. 4 to 6)",
     "4 to 6"),
    ("uses_per_turn", "Realistically how many times per turn does it fire?", "1"),
    ("level_range", "What level range is this designed for? (e.g. 3-12)", "3-12"),
    ("first_level", "Class level the subclass is chosen at (3 for most)", "3"),
    ("primary_ability", "Primary ability: 1=STR 2=DEX 3=CON 4=INT 5=WIS 6=CHA", "1"),
]


def build_cfg(answers: dict, bases: dict, unpacked: str) -> dict:
    """answers -> a complete forge.json config. ONE builder, two callers.

    `init` and `probe` both need every uuid, handle and derived id the templates
    reference. A second copy of this dict would drift the moment either grew a field -
    which is precisely the bug found on 2026-09-01 in a different file, where Divine
    discovery existed in three places and only one of them probed.
    """
    name = answers.get("name", "")
    if not SAFE_NAME.match(name):
        raise ForgeError(
            f"name {name!r} will not work. It becomes a folder, a FixedString and an "
            f"EditorID prefix,\nso: start with a letter, then letters/digits/underscore, "
            f"3-40 characters. No spaces.")
    parent = answers.get("parent", "")
    if parent not in bases:
        raise ForgeError(
            f"{parent!r} is not a base class in your game data. Found: "
            f"{', '.join(sorted(bases))}.\nThis tool will not invent a ParentGuid.")
    return {
        "name": name,
        "parent": parent,
        "parent_uuid": bases[parent]["uuid"],
        "author": answers.get("author") or "unknown",
        "description": answers.get("description") or f"A {parent} subclass.",
        "feature_name": answers.get("feature_name") or "Placeholder Feature",
        "feature_id": f"{name}_Placeholder",
        "first_level": int(answers.get("first_level") or 3),
        "primary_ability": int(answers.get("primary_ability") or 1),
        "tags": "Class;Subclass",
        "mod_uuid": new_uuid(),
        "class_uuid": new_uuid(),
        "table_uuid": new_uuid(),
        "prog_uuid": new_uuid(),
        "progdesc_uuid": new_uuid(),
        "h_name": new_handle(),
        "h_short": new_handle(),
        "h_desc": new_handle(),
        "h_feat_name": new_handle(),
        "h_feat_desc": new_handle(),
        "resource_name": answers.get("resource_name") or "Focus",
        "resource_id": f"{name}Resource",
        "resource_uuid": new_uuid(),
        "spell_name": answers.get("spell_name") or "Placeholder Strike",
        "spell_id": f"Target_{name}_Placeholder",
        "spelllist_uuid": new_uuid(),
        "levelmap_id": f"{name}Die",
        "levelmap_uuid": new_uuid(),
        "h_res_name": new_handle(),
        "h_res_desc": new_handle(),
        "h_spell_name": new_handle(),
        "h_spell_desc": new_handle(),
        "balance": {
            "compare_to": answers.get("compare_to") or "",
            "pool_size": answers.get("pool_size") or "",
            "uses_per_turn": answers.get("uses_per_turn") or "",
            "level_range": answers.get("level_range") or "",
            "_note": "The comparison target is the load-bearing one: 'is this too strong' "
                     "is unanswerable in the abstract and easy against a named baseline.",
        },
        "version64": version64(1, 0, 0, 0),
        "_provenance": {
            "parent_uuid_read_from": str(unpacked),
            "note": "parent_uuid came from the unpacked game data on this machine. "
                    "Every other uuid/handle here was generated fresh. Nothing was "
                    "copied from a guide.",
        },
    }


def init(args) -> int:
    answers: dict = {}
    if args.answers:
        answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
    else:
        print("\nA few questions. The design conversation is with your agent - this only "
              "asks\nwhat the generator physically needs.\n")
        for key, prompt, default in QUESTIONS:
            d = f" [{default}]" if default else ""
            v = input(f"  {prompt}{d}: ").strip()
            if not v and default:
                v = default
            answers[key] = v

    classes = read_classes(Path(args.unpacked))
    bases = base_classes(classes)
    # ⚠ Every id is generated ONCE, here, and written down - so the same values are
    # reused on a re-scaffold instead of silently producing a second, conflicting mod
    # identity. See build_cfg().
    cfg = build_cfg(answers, bases, args.unpacked)
    name, parent = cfg["name"], cfg["parent"]
    out = Path(args.config)
    if out.exists() and not args.force:
        raise ForgeError(f"{out} already exists. Re-running init would mint a NEW mod "
                         f"UUID and\norphan any save that used the old one. Pass --force "
                         f"only if you mean that.")
    out.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")
    print(f"  parent {parent} -> {cfg['parent_uuid']}  (read from your game data)")
    print(f"\nnext: py forge.py scaffold")
    return 0


# ---------------------------------------------------------------- scaffold ----
def scaffold(args) -> int:
    cfgp = Path(args.config)
    if not cfgp.is_file():
        raise ForgeError(f"no {cfgp}. Run `py forge.py init` first.")
    cfg = json.loads(cfgp.read_text(encoding="utf-8"))

    # Re-verify the parent GUID against the game rather than trusting the config. A
    # config can be hand-edited, copied between machines, or filled in from a guide -
    # and a wrong ParentGuid is the failure this whole tool exists to prevent.
    classes = read_classes(Path(args.unpacked))
    bases = base_classes(classes)
    real = bases.get(cfg["parent"], {}).get("uuid")
    if not real:
        raise ForgeError(f"{cfg['parent']!r} is not a base class in your game data.")
    if real != cfg["parent_uuid"]:
        raise ForgeError(
            f"forge.json says {cfg['parent']}'s GUID is {cfg['parent_uuid']}, but your "
            f"game data says {real}.\nRefusing to scaffold. Fix forge.json - a wrong "
            f"ParentGuid validates cleanly and crashes at character creation.")

    root = Path(args.out or cfg["name"])
    if root.exists() and any(root.iterdir()) and not args.force:
        raise ForgeError(f"{root} exists and is not empty. Pass --force to overwrite.")

    written = []
    for rel_t, body_t in FILES:
        rel = fill(rel_t, cfg)
        body = fill(body_t, cfg)
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        written.append(p)

    # ⭐ THE CONFIG MOVES INTO THE MOD ROOT, and this is not tidiness.
    #
    # `init` writes forge.json to the CWD; `scaffold` writes the mod into a SUBDIRECTORY
    # named after it. That left the config a SIBLING of the tree it describes, and
    # modconfig.find() walks UPWARD from the calling file looking for forge.json - so it
    # found this config and resolved every path one directory too high. Against a freshly
    # scaffolded mod, `cfg.public`, `cfg.stats`, `cfg.meta` and `cfg.pak` ALL pointed at
    # paths that do not exist.
    #
    # Nothing caught it because the only mod the toolchain had ever been run against is
    # Warpblade, whose forge.json has always sat inside its own root. The framework worked
    # perfectly on the one layout it was born in and was broken for every mod it produced
    # - which is the exact failure a framework exists to prevent, so it is worth the
    # comment. Found 2026-08-27 by scaffolding a throwaway mod and asking modconfig
    # whether the paths it returned existed. They did not.
    #
    # Moved rather than copied: two forge.json files in a parent/child pair is a trap,
    # because which one wins depends on the directory a tool happens to be run from.
    dest = root / cfgp.name
    if cfgp.resolve() != dest.resolve():
        dest.write_text(cfgp.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            cfgp.unlink()
        except OSError:
            print(f"  ! could not remove {cfgp} - delete it by hand. Two forge.json "
                  f"files in a parent/child pair resolve differently depending on where "
                  f"a tool is run from.")
        # deliberately NOT appended to `written`: that list means "files produced from a
        # FILES template", and it is both counted and XML-validated below. forge.json is
        # neither templated nor XML.
        print(f"  forge.json moved into {root}/ so the tools can find this mod")

    # ⭐ VALIDATE WHAT WE JUST WROTE, before anyone is told it worked. A generator that
    # emits malformed XML is worse than no generator: it hands you a broken file with a
    # trustworthy provenance story attached to it.
    for p in written:
        if p.suffix.lower() in (".lsx", ".xml"):
            try:
                ET.parse(p)
            except ET.ParseError as e:
                raise ForgeError(f"generated {p} is not well-formed XML: {e}. This is a "
                                 f"bug in forge.py, not in your input.")

    # The class icon is written AFTER the XML check because it is not XML, and it is
    # written at all because a missing one is a BLANK entry on the class-select screen -
    # a plumbing failure wearing an art failure's clothes.
    icons = write_class_icon(root, cfg["name"])
    # The icons are useless without this: BG3 rejects them with "missing
    # texture metadata" and names the GUI folder. Found in game 2026-09-02.
    meta_files = write_gui_metadata(root, cfg["name"])
    if meta_files:
        print("GUI texture metadata: %s" % meta_files[0])
    if icons:
        print(f"\nplaceholder class icon written in {len(icons)} size(s). "
              f"It is meant to look temporary.")
    else:
        print("\n⚠ Pillow is not installed, so NO class icon was generated. Your subclass "
              "will show\n  a BLANK entry at character creation until you add one. "
              "`pip install Pillow` and\n  re-run scaffold, or author the four DDS files "
              "by hand.")

    print(f"\nscaffolded {len(written)} file(s) under {root}/\n")
    for p in written:
        print(f"  {p.relative_to(root)}")
    print(f"""
Every generated .lsx parsed as XML before this message printed.

WHAT YOU HAVE: a {cfg['parent']} subclass called {cfg['name']} that appears at level
{cfg['first_level']} and grants one placeholder passive. No design, all plumbing.

NEXT, IN THIS ORDER - and the order is the point:
  1. Pack it and load the game. Confirm {cfg['name']} appears in the {cfg['parent']}
     subclass list with real text and not a raw handle. Do this BEFORE writing any
     content, so that when something breaks later you know the plumbing was fine.
  2. THEN build your validator (see FORGE.md 3.2 and 3.2b). Sort its checks into
     "will it silently do nothing" and "will the game die" - most people only build
     the first column and that is how mods crash constantly.
  3. THEN design. Talk to your agent about what the class actually does.

You will need the Subclass Compatibility Framework installed for the subclass to be
registered at runtime. This mod ships no Lua and overrides no vanilla file.""")
    return 0


PROBE_MD = """# Probe: {{NAME}}

**Question this mod exists to answer:**

> {{QUESTION}}

## Why a probe and not a branch of the real mod

An engine question asked inside a mod with twenty features has twenty candidate causes.
This mod has ONE. If the observation is wrong here, the mechanic is wrong - not the
interaction, not the ordering, not some other passive.

⚠ **This does NOT remove the character reroll.** A subclass is chosen at character
creation, so testing still costs a new character at level {{FIRST_LEVEL}}. What it removes is
everything else: the full gate suite, the other features, and the doubt about which of
them caused what. Say "a probe confirmed X" only about the thing this mod contains.

## Run it

```
cd {{NAME}} && ./build.ps1
```

Then launch, make a {{PARENT}}, take {{NAME}} at level {{FIRST_LEVEL}}, and observe.

## Record the result

| | |
|---|---|
| Question | {{QUESTION}} |
| Observed | *(fill in: exactly what happened, not what you concluded)* |
| Verdict | CONFIRMED / REFUTED / INCONCLUSIVE |
| Date | |

⭐ **A REFUTED probe is worth more than a confirmed one** - it corrects a model that was
about to be built on. Write the observation down before the interpretation, and put the
finding in `docs/bg3-mechanics/` so the next session does not re-ask it.
"""


def probe(args) -> int:
    """Emit the SMALLEST loadable mod that asks one engine question.

    ⭐ WHY THIS COMMAND EXISTS. Live Pass 35 was the stated next action for two whole
    sessions and did not happen, because verifying one assumption cost a full Warpblade
    build, a launch, and a reroll - and then the answer was confounded by twenty other
    features. Mined from the "small isolated test mods" idea in the 2026-09-01 directive
    (work-offline item 107c), which was taken precisely because it attacks the reason the
    top queue item keeps not happening.

    ⚠ IT IS HONEST ABOUT WHAT IT DOES NOT FIX. A subclass is chosen at character
    creation, so the reroll remains. Claiming otherwise would be the "hallucinate
    success" failure the same directive warns about. What it removes is the twenty
    confounders and the full gate suite.
    """
    classes = read_classes(Path(args.unpacked))
    bases = base_classes(classes)
    cfg = build_cfg({
        "name": args.name,
        "parent": args.parent,
        "author": "probe",
        "description": f"Single-question probe: {args.question}",
        "feature_name": f"{args.name} Probe Feature",
        "first_level": args.level,
    }, bases, args.unpacked)
    cfg["question"] = args.question

    root = Path(args.out or args.name)
    if root.exists() and any(root.iterdir()) and not args.force:
        raise ForgeError(f"{root} exists and is not empty. Pass --force to overwrite.")

    # Caller-supplied stats bodies replace the placeholder ones. Everything else is the
    # ordinary scaffold, because the plumbing is not what is under test - and a probe
    # built on different plumbing than the real mod would answer a different question.
    overrides = {}
    if args.passive:
        overrides["Public/{{NAME}}/Stats/Generated/Data/Passive.txt"] = \
            Path(args.passive).read_text(encoding="utf-8")
    if args.spell:
        overrides["Public/{{NAME}}/Stats/Generated/Data/Spell_Target.txt"] = \
            Path(args.spell).read_text(encoding="utf-8")

    written = []
    for rel_t, body_t in FILES + [("PROBE.md", PROBE_MD)]:
        rel = fill(rel_t, cfg)
        raw = overrides.get(rel_t, body_t)
        # An override is VERBATIM: it is the thing under test, so a stray {{TOKEN}} in it
        # must not be silently substituted into something that looks fine.
        body = raw if rel_t in overrides else fill(raw, cfg)
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        written.append(p)

    (root / "forge.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    for p in written:
        if p.suffix.lower() in (".lsx", ".xml"):
            try:
                ET.parse(p)
            except ET.ParseError as e:
                raise ForgeError(f"generated {p} is not well-formed XML: {e}. This is a "
                                 f"bug in forge.py, not in your input.")
    write_class_icon(root, cfg["name"])
    write_gui_metadata(root, cfg["name"])

    print(f"\nprobe '{cfg['name']}' written to {root}/ - {len(written)} file(s)")
    print(f"  question: {args.question}")
    print(f"  {cfg['parent']} subclass, offered at level {cfg['first_level']}")
    if overrides:
        print(f"  {len(overrides)} stats file(s) taken VERBATIM from your input")
    else:
        print("  no --passive/--spell given, so this probes the PLUMBING only: "
              "does a minimal subclass load and appear at all?")
    print("\n⚠ This does not remove the reroll - a subclass is picked at character "
          "creation.\n  It removes the other twenty features as candidate causes.")
    print(f"\nnext: cd {root} && ./build.ps1, then read PROBE.md and fill in the result.")
    return 0


def cmd_classes(args) -> int:
    classes = read_classes(Path(args.unpacked))
    bases = base_classes(classes)
    subs = len(classes) - len(bases)
    print(f"\n{len(bases)} base class(es), {subs} subclass(es), read from "
          f"{args.unpacked}\n")
    for n, c in sorted(bases.items()):
        print(f"  {n:<14} {c['uuid']}")
    print("\nThese are the real GUIDs from YOUR install. Pass one of these names as the "
          "parent.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unpacked", default=str(UNPACKED),
                    help=f"unpacked game data (default {UNPACKED}, or $BG3_UNPACKED)")
    ap.add_argument("--config", default="forge.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="is this machine set up to build a mod")
    sub.add_parser("classes", help="real vanilla classes and their GUIDs")

    i = sub.add_parser("init", help="interview -> forge.json")
    i.add_argument("--answers", help="JSON file of answers, for non-interactive use")
    i.add_argument("--force", action="store_true", help="overwrite an existing forge.json")

    s = sub.add_parser("scaffold", help="forge.json -> a loadable mod tree")
    s.add_argument("--out", help="output directory (default: the mod name)")
    s.add_argument("--force", action="store_true", help="overwrite a non-empty directory")

    pr = sub.add_parser("probe", help="smallest loadable mod that asks ONE engine question")
    pr.add_argument("--name", required=True, help="probe mod name (becomes the folder)")
    pr.add_argument("--question", required=True,
                    help="the single question this mod exists to answer, in one sentence")
    pr.add_argument("--parent", default="Fighter", help="parent class (default Fighter)")
    pr.add_argument("--level", type=int, default=3,
                    help="class level the subclass is offered at (default 3)")
    pr.add_argument("--passive", help="file whose contents REPLACE Passive.txt verbatim")
    pr.add_argument("--spell", help="file whose contents REPLACE Spell_Target.txt verbatim")
    pr.add_argument("--out", help="output directory (default: the probe name)")
    pr.add_argument("--force", action="store_true", help="overwrite a non-empty directory")

    a = ap.parse_args()
    try:
        if a.cmd == "doctor":
            return doctor(Path(a.unpacked))
        if a.cmd == "classes":
            return cmd_classes(a)
        if a.cmd == "init":
            return init(a)
        if a.cmd == "scaffold":
            return scaffold(a)
        if a.cmd == "probe":
            return probe(a)
    except ForgeError as e:
        print(f"\nrefusing: {e}\n", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
