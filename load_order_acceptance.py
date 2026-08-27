# SPDX-License-Identifier: GPL-3.0-or-later
"""Controls for load_order_audit.py.

⚠ SYNTHETIC FIXTURES, NOT THE LIVE GAME. A harness pointed at a real install passes or
    fails depending on which mods happen to be installed today - it cries wolf when the
    data changes and goes quiet when the code breaks. Every case below is constructed.

⚠ THE CONTROL THAT MATTERS MOST IS THE OVERRIDE ONE. The audit's first hand-run reported
    two override paks as "installed but not loaded" - a fault that was not a fault. An
    override is DEFINED by being absent from modsettings while overwriting base-game
    paths, so a tool that calls that broken is worse than no tool: it sends you to
    re-enable something that is already working.

⚠ AND THE INHERITANCE CHECK MUST FAIL WHEN REVERSED. That check exists to catch one real
    ordering requirement, so proving it fires on the wrong order matters more than
    proving it stays quiet on the right one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import load_order_audit as A  # noqa: E402

ok = bad = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        bad += 1
        print(f"  FAIL  {label}" + (f" - {detail}" if detail else ""))


def rec(name="", uuid="", deps=(), paths=(), entries=(), patches=(), hashes=None,
        goals=(), flags=()):
    return {"name": name, "uuid": uuid, "deps": list(deps), "paths": list(paths),
            "entries": list(entries), "patches": list(patches),
            "hashes": dict(hashes or {}), "goals": list(goals),
            "flags": set(flags)}


def order(*names):
    return [{"Name": n, "UUID": f"uuid-{n}"} for n in names]


print("\nload order audit controls\n")

# ---- overrides vs orphans: the distinction the first hand-run got wrong ---------
ovr = rec("Shady", "uuid-Shady",
          paths=["Mods/GustavDev/Story/DialogsBinary/Thing.lsf", "Mods/Shady/meta.lsx"])
orp = rec("Inert", "uuid-Inert", paths=["Public/Inert/Stuff/thing.txt"])
check("a pak writing a BASE-GAME path is an override", bool(A.is_override(ovr)))
check("a pak writing only its own namespace is not", not A.is_override(orp))

f = A.audit(order("Listed"), {"s.pak": ovr, "i.pak": orp,
                              "l.pak": rec("Listed", "uuid-Listed")})
check("an override is reported as an override, NOT as a fault",
      [o["name"] for o in f["overrides"]] == ["Shady"],
      str(f["overrides"]))
check("...and is NOT listed as an orphan",
      "Shady" not in [o["name"] for o in f["orphans"]])
check("an unlisted pak that overrides nothing IS an orphan",
      [o["name"] for o in f["orphans"]] == ["Inert"], str(f["orphans"]))

# ---- ghosts --------------------------------------------------------------------
f = A.audit(order("Real", "Vanished"), {"r.pak": rec("Real", "uuid-Real")})
check("an entry with no pak is a ghost",
      [g["name"] for g in f["ghosts"]] == ["Vanished"], str(f["ghosts"]))
check("...and a real entry is not", "Real" not in [g["name"] for g in f["ghosts"]])

# ---- dependencies --------------------------------------------------------------
paks = {"a.pak": rec("Lib", "uuid-Lib"),
        "b.pak": rec("User", "uuid-User", deps=["Lib"])}
check("dependency loading first is clean",
      not A.audit(order("Lib", "User"), paks)["deps"])
f = A.audit(order("User", "Lib"), paks)
check("dependency loading LATER is a fault",
      [d["problem"] for d in f["deps"]] == ["loads later"], str(f["deps"]))
f = A.audit(order("User"), {"b.pak": paks["b.pak"]})
check("a missing dependency is a fault",
      [d["problem"] for d in f["deps"]] == ["not loaded"], str(f["deps"]))
check("a base-game dependency is never a fault",
      not A.audit(order("Solo"),
                  {"s.pak": rec("Solo", "uuid-Solo", deps=[])}) ["deps"],
      "GustavDev and friends are filtered at extraction")

# ---- path conflicts ------------------------------------------------------------
p1 = rec("First", "uuid-First", paths=["Public/Game/GUI/thing.dds"])
p2 = rec("Second", "uuid-Second", paths=["Public/Game/GUI/thing.dds"])
f = A.audit(order("First", "Second"), {"1.pak": p1, "2.pak": p2})
check("two mods writing one path is a conflict", len(f["path_conflicts"]) == 1)
check("...and the LATER one is named the winner",
      f["path_conflicts"][0]["winner"] == "Second", str(f["path_conflicts"]))
check("different paths are not a conflict",
      not A.audit(order("First", "Third"),
                  {"1.pak": p1,
                   "3.pak": rec("Third", "uuid-Third",
                                paths=["Public/Game/GUI/other.dds"])})["path_conflicts"])

# ---- stats entry conflicts, which share NO path --------------------------------
e1 = rec("Alpha", "uuid-Alpha", paths=["Public/Alpha/a.txt"], entries=["SHARED_ENTRY"])
e2 = rec("Beta", "uuid-Beta", paths=["Public/Beta/b.txt"], entries=["SHARED_ENTRY"])
f = A.audit(order("Alpha", "Beta"), {"a.pak": e1, "b.pak": e2})
check("same entry from DIFFERENT files is a conflict", len(f["entry_conflicts"]) == 1,
      "this is the case a path comparison cannot see")
check("...and it shares no path", not f["path_conflicts"])
check("...and the later mod wins", f["entry_conflicts"][0]["winner"] == "Beta")

# ⭐ IDENTICAL DEFINITIONS ARE NOT A CONFLICT. Two mods very often ship the same entry -
# both derived from the same 5e source, or one vendoring the other. Calling that a
# finding teaches the reader to skim the section that also carries the real ones.
same_a = rec("Same1", "u1", paths=["Public/S1/a.txt"], entries=["E"], hashes={"E": "aaa"})
same_b = rec("Same2", "u2", paths=["Public/S2/b.txt"], entries=["E"], hashes={"E": "aaa"})
f = A.audit(order("Same1", "Same2"), {"1.pak": same_a, "2.pak": same_b})
check("two mods defining an entry IDENTICALLY is flagged identical",
      f["entry_conflicts"] and f["entry_conflicts"][0]["identical"] is True,
      "byte-identical definitions mean the order between them decides nothing")

diff_b = rec("Diff2", "u3", paths=["Public/D2/b.txt"], entries=["E"], hashes={"E": "bbb"})
f = A.audit(order("Same1", "Diff2"), {"1.pak": same_a, "2.pak": diff_b})
check("...and DIFFERING definitions are flagged as a real conflict",
      f["entry_conflicts"] and f["entry_conflicts"][0]["identical"] is False)
check("...with the later mod named the winner",
      f["entry_conflicts"][0]["winner"] == "Diff2")

nohash_b = rec("NoHash", "u4", paths=["Public/N/b.txt"], entries=["E"])
f = A.audit(order("Same1", "NoHash"), {"1.pak": same_a, "2.pak": nohash_b})
check("a MISSING hash is never treated as identical",
      f["entry_conflicts"][0]["identical"] is False,
      "unknown must fall to the cautious side, not the quiet one")

# ---- ⭐ inheritance, both directions --------------------------------------------
base = rec("Base", "uuid-Base", paths=["Public/Base/x.txt"], entries=["ENT"])
patch = rec("Patch", "uuid-Patch", paths=["Public/Patch/x.txt"],
            entries=["ENT"], patches=["ENT"])

f = A.audit(order("Base", "Patch"), {"b.pak": base, "p.pak": patch})
check("a patcher loading AFTER what it patches is ok",
      f["inheritance"] and all(i["ok"] for i in f["inheritance"]), str(f["inheritance"]))

f = A.audit(order("Patch", "Base"), {"b.pak": base, "p.pak": patch})
check("⭐ a patcher loading BEFORE what it patches is caught",
      f["inheritance"] and not any(i["ok"] for i in f["inheritance"]),
      "the whole reason this check exists - it must FAIL on the wrong order")
check("...and it names both sides and the entry",
      f["inheritance"][0]["patcher"] == "Patch"
      and f["inheritance"][0]["patches"] == "Base"
      and f["inheritance"][0]["entry"] == "ENT")
check("a self-`using` with nobody else defining it is not an ordering claim",
      not A.audit(order("Patch"), {"p.pak": patch})["inheritance"],
      "patching an entry no other installed mod defines constrains nothing")

# ---- Osiris: goals and flags ---------------------------------------------------
# ⭐ THE LAYER STORY MODS ACTUALLY CONFLICT IN. A goal file is the unit BG3 executes, so
# two mods writing the same one means the loser's story simply never runs.
g1 = rec("Story1", "g1", paths=["Public/S1/x"], goals=["Mods/Story1/Story/RawFiles/Goals/A.txt"])
g2 = rec("Story2", "g2", paths=["Public/S2/x"], goals=["Mods/Story1/Story/RawFiles/Goals/A.txt"])
f = A.audit(order("Story1", "Story2"), {"1.pak": g1, "2.pak": g2})
check("two mods writing the same GOAL file is a conflict",
      len(f["goal_conflicts"]) == 1 and f["goal_conflicts"][0]["winner"] == "Story2",
      str(f["goal_conflicts"]))
check("...and separate goal files are not",
      not A.audit(order("Story1", "Story3"),
                  {"1.pak": g1,
                   "3.pak": rec("Story3", "g3", paths=["Public/S3/x"],
                                goals=["Mods/Story3/Story/RawFiles/Goals/B.txt"])}
                  )["goal_conflicts"])

base_goal = rec("Overrider", "g4", paths=["Public/O/x"],
                goals=["Mods/GustavDev/Story/RawFiles/Goals/XJA_Thing.txt"])
f = A.audit(order("Overrider"), {"o.pak": base_goal})
check("a goal written into a BASE-GAME module is flagged as a story override",
      f["story_overrides"] and f["story_overrides"][0]["module"] == "GustavDev",
      str(f["story_overrides"]))

fl1 = rec("F1", "f1", paths=["Public/F1/x"], flags=["MY_SHARED_FLAG"])
fl2 = rec("F2", "f2", paths=["Public/F2/x"], flags=["MY_SHARED_FLAG"])
f = A.audit(order("F1", "F2"), {"1.pak": fl1, "2.pak": fl2})
check("two mods writing the same named FLAG is reported",
      len(f["shared_flags"]) == 1 and f["shared_flags"][0]["flag"] == "MY_SHARED_FLAG")
check("...and different flags are not",
      not A.audit(order("F1", "F3"),
                  {"1.pak": fl1,
                   "3.pak": rec("F3", "f3", paths=["Public/F3/x"], flags=["OTHER"])}
                  )["shared_flags"])

# ---- refusals ------------------------------------------------------------------
try:
    A.read_order(Path("Z:/definitely/not/here/modsettings.lsx"))
    check("a missing modsettings refuses", False, "it returned instead of raising")
except A.AuditError:
    check("a missing modsettings refuses", True)

import tempfile  # noqa: E402
empty = Path(tempfile.mkdtemp()) / "modsettings.lsx"
empty.write_text("<save><region id='ModuleSettings'></region></save>", encoding="utf-8")
try:
    A.read_order(empty)
    check("an EMPTY load order refuses rather than reporting clean", False)
except A.AuditError:
    check("an EMPTY load order refuses rather than reporting clean", True,
          "zero mods parsed is a parse failure, not a tidy install")

print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
