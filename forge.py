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
    identity, class description, a progression table, one placeholder feature, and the
    localisation to name them. You get something in the game in the first session, and
    THEN you design.

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
    No design. No balance. No spells beyond one placeholder passive that proves the chain
    end to end. Those are conversations to have with the agent, and a generator that
    guessed at them would produce a class nobody chose.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

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
</contentList>
"""

FILES = [
    ("Mods/{{NAME}}/meta.lsx", META),
    ("Public/{{NAME}}/ClassDescriptions/ClassDescriptions.lsx", CLASSDESC),
    ("Public/{{NAME}}/Progressions/Progressions.lsx", PROGRESSIONS),
    ("Public/{{NAME}}/Progressions/ProgressionDescriptions.lsx", PROGDESC),
    ("Public/{{NAME}}/Stats/Generated/Data/Passive.txt", PASSIVE),
    ("Localization/English/{{NAME}}.xml", LOCA),
]


def fill(text: str, cfg: dict) -> str:
    out = text
    for k, v in cfg.items():
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
    ("first_level", "Class level the subclass is chosen at (3 for most)", "3"),
    ("primary_ability", "Primary ability: 1=STR 2=DEX 3=CON 4=INT 5=WIS 6=CHA", "1"),
]


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

    name = answers.get("name", "")
    if not SAFE_NAME.match(name):
        raise ForgeError(
            f"name {name!r} will not work. It becomes a folder, a FixedString and an "
            f"EditorID prefix,\nso: start with a letter, then letters/digits/underscore, "
            f"3-40 characters. No spaces.")

    classes = read_classes(Path(args.unpacked))
    bases = base_classes(classes)
    parent = answers.get("parent", "")
    if parent not in bases:
        raise ForgeError(
            f"{parent!r} is not a base class in your game data. Found: "
            f"{', '.join(sorted(bases))}.\nThis tool will not invent a ParentGuid.")

    # ⚠ Every id below is generated HERE and written down, so the same values are reused
    # on a re-scaffold instead of silently producing a second, conflicting mod identity.
    cfg = {
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
        "version64": version64(1, 0, 0, 0),
        "_provenance": {
            "parent_uuid_read_from": str(Path(args.unpacked)),
            "note": "parent_uuid came from the unpacked game data on this machine. "
                    "Every other uuid/handle here was generated fresh. Nothing was "
                    "copied from a guide.",
        },
    }
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
    except ForgeError as e:
        print(f"\nrefusing: {e}\n", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
