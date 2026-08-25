"""Generate the deliberately-broken mods in fixtures/. Run rarely; commit the output.

    py make_fixtures.py            # regenerate fixtures/ from forge.py's own templates
    py make_fixtures.py --check    # regenerate into a temp dir and diff (no writes)

⭐ WHY FIXTURES EXIST AT ALL
    Every crash-class check in this toolchain is a GUESS. Warpblade has never crashed
    BG3, so nothing here was written by watching a crash - checks 15 and 16 were reasoned
    out from the shape of the bug and shipped without ever catching one. Rule 4.15: a
    check list built from your own experience has a hole shaped like what never happened
    to you.

    A fixture is the missing half. Break a mod ON PURPOSE, in a way documented down to
    the file and the symptom, and a checker can finally be wrong in a way you can see.

⚠ GENERATED FROM forge.py's OWN TEMPLATES, NEVER HAND-WRITTEN.
    A hand-written fixture is a snapshot of what the generator emitted on the day someone
    typed it out. The generator moves; the fixture does not; and then the checks are being
    proved against a mod shape that forge.py has not produced for months - passing while
    the real output goes unexamined. So `healthy` is literally `scaffold` output, and every
    broken fixture is `healthy` plus ONE documented mutation.

⚠ ONE BREAK PER FIXTURE, AND EVERYTHING ELSE STAYS VALID.
    Two breaks in one fixture cannot distinguish "the check I am testing fired" from "some
    other check fired first". In particular every fixture except `xml-malformed` must
    still parse as XML, or check 15 would catch them all and the specific check under test
    would never be exercised. `fixtures_acceptance.py` asserts exactly that.

⚠ THE UUIDs HERE ARE DETERMINISTIC, AND THAT IS NOT HOW A REAL MOD WORKS.
    forge.py mints a random UUID per mod, correctly - two mods sharing one is a real
    conflict. Fixtures need the opposite: regenerating must produce a byte-identical tree,
    or every run churns the diff and nobody can see which byte the mutation changed. So
    these are uuid5 of a fixed namespace. They are TEST DATA. Do not lift one into a mod.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forge as F  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "fixtures"

# Fixed namespace so regeneration is byte-stable. See the docstring warning.
NS = uuid.UUID("11111111-2222-3333-4444-555555555555")
NAME = "FixtureBlade"


def det_uuid(tag: str) -> str:
    return str(uuid.uuid5(NS, tag))


def det_handle(tag: str) -> str:
    return "h" + str(uuid.uuid5(NS, "handle:" + tag)).replace("-", "g")


def base_cfg(parent: str, parent_uuid: str) -> dict:
    return {
        "name": NAME,
        "parent": parent,
        "parent_uuid": parent_uuid,
        "author": "fixtures",
        "description": "A deliberately-shaped test subclass. Not a real mod.",
        "feature_name": "Fixture Feature",
        "feature_id": f"{NAME}_Placeholder",
        "first_level": 3,
        "primary_ability": 1,
        "tags": "Class;Subclass",
        "mod_uuid": det_uuid("mod"),
        "class_uuid": det_uuid("class"),
        "table_uuid": det_uuid("table"),
        "prog_uuid": det_uuid("prog"),
        "progdesc_uuid": det_uuid("progdesc"),
        "h_name": det_handle("name"),
        "h_short": det_handle("short"),
        "h_desc": det_handle("desc"),
        "h_feat_name": det_handle("feat_name"),
        "h_feat_desc": det_handle("feat_desc"),
        "version64": F.version64(1, 0, 0, 0),
    }


def render(cfg: dict) -> dict[str, str]:
    """The healthy tree: exactly what `forge.py scaffold` writes, as {relpath: text}."""
    return {F.fill(rel, cfg): F.fill(tpl, cfg) for rel, tpl in F.FILES}


# --------------------------------------------------------------- mutations ----
# Each takes the healthy tree and returns it broken in ONE way. Keep them surgical:
# a mutation that rewrites a whole file makes it impossible to see what changed.

def m_xml_malformed(t: dict, cfg: dict) -> dict:
    p = f"Public/{NAME}/Progressions/Progressions.lsx"
    # Drop the closing tag of the region. LSLib's own writer never emits this, but a
    # hand-edit does, and the toolchain had no XML parser in it at all until check 15.
    t[p] = t[p].replace("    </region>\n", "", 1)
    return t


def m_using_cycle(t: dict, cfg: dict) -> dict:
    p = f"Public/{NAME}/Stats/Generated/Data/Passive.txt"
    t[p] += (
        f'\nnew entry "{NAME}_CycleA"\n'
        f'type "PassiveData"\n'
        f'using "{NAME}_CycleB"\n'
        f'data "DisplayName" "{cfg["h_feat_name"]};1"\n'
        f'\nnew entry "{NAME}_CycleB"\n'
        f'type "PassiveData"\n'
        f'using "{NAME}_CycleA"\n'
        f'data "DisplayName" "{cfg["h_feat_name"]};1"\n')
    return t


def m_dangling_passive(t: dict, cfg: dict) -> dict:
    p = f"Public/{NAME}/Progressions/Progressions.lsx"
    t[p] = t[p].replace(f'id="PassivesAdded" type="LSString" value="{cfg["feature_id"]}"',
                        f'id="PassivesAdded" type="LSString" value="{NAME}_NoSuchPassive"')
    return t


def m_table_uuid_mismatch(t: dict, cfg: dict) -> dict:
    p = f"Public/{NAME}/ClassDescriptions/ClassDescriptions.lsx"
    t[p] = t[p].replace(f'id="ProgressionTableUUID" type="guid" value="{cfg["table_uuid"]}"',
                        f'id="ProgressionTableUUID" type="guid" value="{det_uuid("wrong-table")}"')
    return t


def m_unresolvable_parent(t: dict, cfg: dict) -> dict:
    p = f"Public/{NAME}/ClassDescriptions/ClassDescriptions.lsx"
    # Well-FORMED and completely wrong - the case a shape check cannot see. This is the
    # exact failure forge.py refuses to create, which is why it needs a fixture.
    t[p] = t[p].replace(f'id="ParentGuid" type="guid" value="{cfg["parent_uuid"]}"',
                        f'id="ParentGuid" type="guid" value="{det_uuid("no-such-class")}"')
    return t


def m_missing_loca_handle(t: dict, cfg: dict) -> dict:
    p = f"Localization/English/{NAME}.xml"
    keep = [ln for ln in t[p].splitlines(keepends=True)
            if cfg["h_feat_name"] not in ln]
    t[p] = "".join(keep)
    return t


def m_duplicate_uuid(t: dict, cfg: dict) -> dict:
    p = f"Public/{NAME}/Progressions/Progressions.lsx"
    t[p] = t[p].replace(f'id="UUID" type="guid" value="{cfg["prog_uuid"]}"',
                        f'id="UUID" type="guid" value="{cfg["class_uuid"]}"')
    return t


def m_dependency_absent(t: dict, cfg: dict) -> dict:
    p = f"Mods/{NAME}/meta.lsx"
    dep = (
        '                <node id="Dependencies">\n'
        '                    <children>\n'
        '                        <node id="ModuleShortDesc">\n'
        '                            <attribute id="Folder" type="LSString" value="NotInstalledModule"/>\n'
        '                            <attribute id="MD5" type="LSString" value=""/>\n'
        '                            <attribute id="Name" type="LSString" value="NotInstalledModule"/>\n'
        f'                            <attribute id="UUID" type="FixedString" value="{det_uuid("absent-dep")}"/>\n'
        '                            <attribute id="Version64" type="int64" value="36028797018963968"/>\n'
        '                        </node>\n'
        '                    </children>\n'
        '                </node>\n')
    t[p] = t[p].replace('                <node id="Dependencies"/>\n', dep, 1)
    return t


# id -> (mutation, what is wrong, in-game symptom, severity, the check that should catch it)
FIXTURES = [
    ("healthy", None,
     "Nothing. This is unmodified `forge.py scaffold` output.",
     "Subclass appears at character creation with real text.",
     "control",
     "every check must stay SILENT on this - a checker that flags the control is worse "
     "than no checker, because it trains you to ignore output"),

    ("xml-malformed", m_xml_malformed,
     "Progressions.lsx is missing its closing </region>.",
     "The mod fails to load, or loads with the progression table absent.",
     "crash",
     "check 15 - every .lsx parses as XML"),

    ("using-cycle", m_using_cycle,
     "Two passives in Passive.txt name each other with `using`.",
     "Stats resolution never terminates on that entry.",
     "crash",
     "check 16 - `using` chains terminate"),

    ("dangling-passive", m_dangling_passive,
     "Progressions grants FixtureBlade_NoSuchPassive, which no stats file defines.",
     "CRASH AT LEVEL-UP - after the player has already committed to the choice, which "
     "is what makes this one worse than it looks.",
     "crash",
     "NOT YET IMPLEMENTED - every name in PassivesAdded resolves to a stats entry"),

    ("table-uuid-mismatch", m_table_uuid_mismatch,
     "ClassDescriptions.ProgressionTableUUID does not equal Progressions.TableUUID.",
     "The subclass silently does not appear. No error anywhere.",
     "silent",
     "NOT YET IMPLEMENTED - the two UUIDs agree. forge.py's template WARNS about this "
     "in a comment; a comment is not a check"),

    ("unresolvable-parent", m_unresolvable_parent,
     "ParentGuid is a well-formed GUID that matches no class in the game data.",
     "Crash or empty subclass list at character creation.",
     "crash",
     "NOT YET IMPLEMENTED - ParentGuid resolves against real game data. A shape check "
     "cannot see this: the value is a perfectly valid GUID"),

    ("missing-loca-handle", m_missing_loca_handle,
     "The feature's DisplayName handle has no entry in the localisation file.",
     "The raw handle string shows in the UI instead of a name.",
     "silent",
     "NOT YET IMPLEMENTED - every handle referenced has loca content"),

    ("duplicate-uuid", m_duplicate_uuid,
     "The Progression node reuses the ClassDescription's UUID.",
     "Undefined - one record shadows the other, and which one wins is not stable.",
     "silent",
     "NOT YET IMPLEMENTED - no UUID appears twice in the mod"),

    ("dependency-absent", m_dependency_absent,
     "meta.lsx declares a dependency on a module that is not installed.",
     "The mod is skipped, or the load order refuses to launch.",
     "crash",
     "NOT YET IMPLEMENTED - declared dependencies exist"),
]


def build(dest: Path, parent: str, parent_uuid: str) -> dict:
    cfg = base_cfg(parent, parent_uuid)
    manifest = {
        "_readme": "Generated by make_fixtures.py. Do not hand-edit - regenerate.",
        "mod_name": NAME,
        "parent": parent,
        "fixtures": [],
    }
    for fid, mut, breaks, symptom, sev, expect in FIXTURES:
        tree = render(cfg)
        if mut:
            tree = mut(tree, cfg)
        root = dest / fid
        if root.exists():
            shutil.rmtree(root)
        for rel, text in tree.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")
        manifest["fixtures"].append({
            "id": fid, "breaks": breaks, "symptom": symptom,
            "severity": sev, "expect": expect,
            "still_parses_as_xml": fid != "xml-malformed",
        })
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                        encoding="utf-8", newline="\n")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unpacked", default=str(F.UNPACKED))
    ap.add_argument("--parent", default="Fighter")
    ap.add_argument("--check", action="store_true",
                    help="build into a temp dir and report drift instead of writing")
    a = ap.parse_args()

    # Same refusal as the rest of the forge: the parent GUID is READ, never invented.
    # A fixture carrying a made-up vanilla GUID would be testing the wrong thing.
    try:
        bases = F.base_classes(F.read_classes(Path(a.unpacked)))
    except F.ForgeError as e:
        print(f"cannot generate fixtures: {e}\n\nThe parent class GUID has to come from "
              f"your unpacked game data. Fixtures are\ncommitted, so this only matters "
              f"when regenerating them.", file=sys.stderr)
        return 2
    if a.parent not in bases:
        print(f"{a.parent!r} is not a base class in your game data.", file=sys.stderr)
        return 2
    puid = bases[a.parent]["uuid"]

    if a.check:
        with tempfile.TemporaryDirectory() as td:
            build(Path(td), a.parent, puid)
            drift = []
            for f in sorted(Path(td).rglob("*")):
                if f.is_file():
                    rel = f.relative_to(td)
                    live = OUT / rel
                    if not live.is_file():
                        drift.append(f"missing on disk: {rel}")
                    elif live.read_bytes().replace(b"\r\n", b"\n") != f.read_bytes():
                        drift.append(f"differs: {rel}")
            for d in drift:
                print(f"  {d}")
            print(f"\n{len(drift)} file(s) drifted from what the templates produce now."
                  if drift else "\nfixtures match the current templates.")
            return 1 if drift else 0

    OUT.mkdir(parents=True, exist_ok=True)
    m = build(OUT, a.parent, puid)
    print(f"\nwrote {len(m['fixtures'])} fixture(s) to {OUT}")
    for f in m["fixtures"]:
        print(f"  {f['severity']:<7} {f['id']:<22} {f['breaks'][:60]}")
    print(f"\nparent {a.parent} -> {puid}  (read from your game data)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
