# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance harness for forge.py.

The value of this tool is almost entirely in WHAT IT REFUSES TO DO. A generator that
happily emits a plausible-but-wrong ParentGuid is worse than no generator, because the
output carries a provenance story that makes it look checked. So most of the controls
below assert a refusal, and each one is paired with the healthy case to prove the refusal
is aimed rather than indiscriminate.
"""
import json
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forge as F

OK = True


def check(label, got, want=True):
    global OK
    good = got == want
    OK = OK and good
    print(f"  {'PASS' if good else 'FAIL'}  {label}"
          + ("" if good else f"   got={got!r} want={want!r}"))


def refuses(label, fn):
    global OK
    try:
        fn()
    except F.ForgeError:
        print(f"  PASS  {label}")
        return
    except Exception as e:
        OK = False
        print(f"  FAIL  {label}   raised {type(e).__name__}, not ForgeError: {e}")
        return
    OK = False
    print(f"  FAIL  {label}   DID NOT REFUSE")


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


HAVE_GAME = Path(F.UNPACKED).is_dir() and bool(F.unpacked_class_files(Path(F.UNPACKED)))

print("identity generation:")
ids = [F.new_uuid() for _ in range(200)]
check("generated UUIDs are unique", len(set(ids)), 200)
check("...and are real UUIDs", all(uuid.UUID(i) for i in ids))
h = F.new_handle()
check("a handle looks like the game's own shape",
      h.startswith("h") and h.count("g") == 4 and len(h) == 37)
check("handles are unique too", len({F.new_handle() for _ in range(200)}), 200)
# Larian's packing. Getting a shift wrong makes a mod manager display 0.0.0.0, which is
# the kind of thing nobody notices until a user reports the wrong version.
check("version64 packs v1.0.0.0", F.version64(1, 0, 0, 0), 36028797018963968)
check("version64 packs v1.8.0.0", F.version64(1, 8, 0, 0), 37154696925806592)

print("\ntemplate filling:")
check("a filled template keeps no placeholders",
      "{{" not in F.fill("<a>{{NAME}}</a>", {"name": "X"}))
# ⚠ Shipping a literal {{NAME}} into a game file is a silent content bug - the game
# reads it as a name. Better to refuse than to emit it.
refuses("an unfilled placeholder refuses rather than shipping",
        lambda: F.fill("<a>{{MISSING}}</a>", {"name": "X"}))

print("\nreading the game (the refusals that matter most):")
refuses("no unpacked data -> refuse, never guess a class GUID",
        lambda: F.read_classes(Path(tempfile.mkdtemp())))
empty = Path(tempfile.mkdtemp())
(empty / "Shared").mkdir()
refuses("a PARTIAL unpack refuses too, rather than reporting 0 classes",
        lambda: F.read_classes(empty))
bad = Path(tempfile.mkdtemp()) / "X/ClassDescriptions"
bad.mkdir(parents=True)
(bad / "ClassDescriptions.lsx").write_text("<save><node>", encoding="utf-8")
refuses("malformed vanilla XML refuses rather than half-parsing",
        lambda: F.read_classes(bad.parent.parent))

if not HAVE_GAME:
    print("\n  SKIP: no unpacked game data on this machine, so the scaffold controls")
    print("        below cannot run. That is a skip, not a pass.")
else:
    classes = F.read_classes(Path(F.UNPACKED))
    bases = F.base_classes(classes)
    check("the twelve base classes are found", len(bases), 12)
    check("subclasses are excluded from the base list", len(classes) > len(bases))
    check("Fighter's GUID matches the one verified from Shared.pak by hand",
          bases["Fighter"]["uuid"], "721dfac3-92d4-41f5-b773-b7072a86232f")
    check("every base class has a progression table",
          all(c["progression_table"] for c in bases.values()))

    def scaffold_in(cfg_patch=None, force=False):
        d = Path(tempfile.mkdtemp())
        a = Args(unpacked=str(F.UNPACKED), config=str(d / "forge.json"),
                 answers=str(d / "a.json"), force=True, out=str(d / "mod"))
        (d / "a.json").write_text(json.dumps({
            "name": "Testclass", "parent": "Fighter", "author": "t",
            "description": "d", "feature_name": "F", "first_level": "3",
            "primary_ability": "1"}), encoding="utf-8")
        F.init(a)
        if cfg_patch:
            c = json.loads(Path(a.config).read_text(encoding="utf-8"))
            c.update(cfg_patch)
            Path(a.config).write_text(json.dumps(c), encoding="utf-8")
        a.force = force
        return a, d

    print("\nscaffolding:")
    a, d = scaffold_in()
    check("a healthy config scaffolds", F.scaffold(a), 0)
    made = sorted(p.relative_to(Path(a.out)).as_posix()
                  for p in Path(a.out).rglob("*") if p.is_file())
    # Count derived from FILES, not typed. A literal here is the "Eleven checks" bug in
    # miniature: it went stale the moment the scaffold grew a spell chain, and a test
    # that fails when the code correctly changes is a test people learn to edit rather
    # than read.
    # Split the count: templated game files vs the generated placeholder icon. Lumping
    # them made this assert fail the moment the icon writer landed, which is a test
    # objecting to correct work - the thing that teaches people to edit tests instead of
    # reading them.
    templated = [m for m in made if "/GUI/" not in m]
    check("it writes every file FILES declares", len(templated), len(F.FILES))
    check("and that includes the whole spell chain",
          all(any(k in m for m in templated) for k in
              ("ActionResourceDefinitions", "SpellLists", "Spell_Target", "Levelmaps")),
          True)
    # ⭐ A missing class icon is a BLANK entry at character creation - a plumbing failure,
    # not an art one, which is why a generator that ships no design still ships this.
    icons = sorted(m for m in made if m.endswith(".DDS"))
    check("a placeholder class icon is written in all four sizes", len(icons), 4)
    check("...at the paths a working mod actually uses",
          all(any(p in m for m in icons) for p in
              ("Assets/ClassIcons/", "Assets/ClassIcons/hotbar/",
               "AssetsLowRes/ClassIcons/", "AssetsLowRes/ClassIcons/hotbar/")), True)
    check("...and a .png beside each, for texconv or hand-editing",
          len([m for m in made if m.endswith(".png")]), 4)
    check("every generated .lsx/.xml is well-formed XML",
          all(ET.parse(p) is not None for p in Path(a.out).rglob("*")
              if p.suffix.lower() in (".lsx", ".xml")))
    cd = next(Path(a.out).rglob("ClassDescriptions.lsx")).read_text(encoding="utf-8")
    check("the REAL parent GUID is written into the file",
          "721dfac3-92d4-41f5-b773-b7072a86232f" in cd)
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    check("ProgressionTableUUID matches the progression rows' TableUUID",
          cfg["table_uuid"] in next(Path(a.out).rglob("Progressions.lsx"))
          .read_text(encoding="utf-8"))
    check("provenance is recorded, so the GUID's origin is not folklore",
          "parent_uuid_read_from" in cfg["_provenance"])

    # ⭐ THE ONE THAT MATTERS. A forge.json can be hand-edited, copied between machines,
    # or filled in from a guide. A wrong ParentGuid validates cleanly, packs cleanly, and
    # crashes at character creation - so the value is re-checked against the game at
    # scaffold time rather than trusted because it is written down.
    print("\nthe GUID re-check (the single most valuable refusal here):")
    a2, _ = scaffold_in({"parent_uuid": str(uuid.uuid4())}, force=True)
    refuses("a parent GUID that disagrees with the game refuses to scaffold",
            lambda: F.scaffold(a2))
    a3, _ = scaffold_in({"parent": "Notaclass"}, force=True)
    refuses("a parent that is not a class at all refuses", lambda: F.scaffold(a3))

    print("\ninit guards:")
    def init_with(answers, force=True):
        d2 = Path(tempfile.mkdtemp())
        (d2 / "a.json").write_text(json.dumps(answers), encoding="utf-8")
        return Args(unpacked=str(F.UNPACKED), config=str(d2 / "forge.json"),
                    answers=str(d2 / "a.json"), force=force)

    base = {"name": "Testclass", "parent": "Fighter", "author": "t", "description": "d",
            "feature_name": "F", "first_level": "3", "primary_ability": "1"}
    refuses("a name with spaces refuses - it becomes a folder and a FixedString",
            lambda: F.init(init_with({**base, "name": "My Class"})))
    refuses("a name starting with a digit refuses",
            lambda: F.init(init_with({**base, "name": "9Lives"})))
    refuses("an unknown parent refuses rather than inventing a GUID",
            lambda: F.init(init_with({**base, "parent": "Artificer"})))
    check("a good name is accepted", F.init(init_with(base)), 0)

    # Re-running init would mint a NEW mod UUID. Any save referencing the old one would
    # lose the mod, so this must be a deliberate act rather than a re-run.
    aa = init_with(base, force=False)
    F.init(Args(**{**aa.__dict__, "force": True}))
    refuses("re-running init will not silently mint a new mod UUID",
            lambda: F.init(aa))

print("\n" + ("ALL GREEN" if OK else "SOMETHING FAILED"))
sys.exit(0 if OK else 1)
