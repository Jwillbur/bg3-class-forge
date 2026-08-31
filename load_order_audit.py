# SPDX-License-Identifier: GPL-3.0-or-later
"""Audit a Baldur's Gate 3 load order against what the installed mods actually contain.

    py load_order_audit.py              # full audit
    py load_order_audit.py --quick      # skip pak extraction (modsettings only)
    py load_order_audit.py --json       # machine-readable

⭐ WHY THIS EXISTS
    Reordering a load order by reading mod NAMES is guesswork, and it produces confident
    nonsense. The first hand attempt at auditing this install moved 21 of 27 entries into
    invented "bands" and missed the only pair of mods that genuinely interact - because
    the thing that made them interact was a `using` clause inside a stats file, which no
    amount of reading titles can see.

    Everything here is measured from the paks: their declared dependencies, the file
    paths they write, and the stats entries they define.

WHAT IT CHECKS, and why each one is a real fault

  1 GHOSTS          - an entry in modsettings with no pak behind it.
  2 ORPHANS         - a pak that is neither listed nor an override: inert, silently.
  3 OVERRIDES       - a pak absent from modsettings that writes into a BASE-GAME
                      namespace. These are NOT broken; that is how an override works.
                      Reported so you know they are active, with the caveat below.
  4 DEPENDENCIES    - a declared dependency must be present and load FIRST.
  5 PATH CONFLICTS  - two mods writing the same file. Later wins.
  6 ENTRY CONFLICTS - two mods defining the same stats entry from DIFFERENT files. No
                      shared path, real conflict, and invisible to a path comparison.
  7 ⭐ INHERITANCE  - a mod defining `new entry "X"` with `using "X"` is patching whatever
                      already defines X, so it MUST load after that mod. Get it backwards
                      and the patch resolves to nothing and is then overwritten wholesale.
                      This is the check that found the one real ordering requirement in a
                      27-mod list, and the one a human reading names will never find.
  8 STORY (Osiris)  - two mods writing the same GOAL file: the later replaces the earlier
                      wholesale, and the earlier mod's story simply never runs. Plus which
                      mods write goals into a BASE-GAME module, overriding the game's own.
  9 SHARED FLAGS    - two mods writing the same named flag are coupled through world
                      state whether they intended it or not.

⚠ SHARED EVENTS ARE NOT REPORTED, ON PURPOSE. Half of any install reacts to LevelLoaded,
    AddedTo and EntityEvent. Osiris rules are ADDITIVE - every matching rule fires - so
    two mods hooking one event is normal rather than a conflict. Listing it would bury
    checks 8 and 9 under a wall of ordinary behaviour, which is how a report stops being
    read at all.

⚠ ON OVERRIDES. A pak that is not in modsettings is treated as disabled - but one that
    overwrites vanilla file paths may still take effect, and its precedence is NOT
    guaranteed. Since Patch 8 the shipped game paks can win against anything in the user
    Mods folder; the reliable location for an override is the game install's own Data
    folder. Sources: NexusMods.App developer docs for BG3, and bg3modmanager.net.

⚠ WHAT IT CANNOT SEE, stated narrowly because the vague version of this line got used
    twice as a reason to flag a worry instead of measuring one: two mods adding SEPARATE
    content to the same moment. Two camp scenes queued for the same night; two encounters
    placed in one room. Nothing is overwritten, so there is nothing to detect - and
    REORDERING DOES NOT HELP either, because both simply run. That is a design clash
    between two mods, not a load-order fault, and the only tool for it is playing.
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
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ⚠ Windows consoles default to cp1252, which cannot encode the warning glyphs this
# report uses - the tool CRASHED mid-report on its first run, after printing half the
# findings. A diagnostic that dies partway through is worse than one that never ran,
# because the half it printed looks like the whole answer.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                           # noqa: BLE001
        pass

LOCALAPP = Path(os.environ.get("LOCALAPPDATA", ""))
PROFILE = LOCALAPP / "Larian Studios" / "Baldur's Gate 3" / "PlayerProfiles" / "Public"
MODS_DIR = LOCALAPP / "Larian Studios" / "Baldur's Gate 3" / "Mods"
MODSETTINGS = PROFILE / "modsettings.lsx"

DIVINE_CANDIDATES = [
    Path(os.environ.get("BG3_DIVINE", "")),
    Path(r"C:\Modding\tools\lslib\Packed\Tools\Divine.exe"),
]

# Modules the base game always provides. A dependency on one of these is never a fault.
BASE_MODULES = {"GustavDev", "Gustav", "Shared", "SharedDev", "GustavX", "Honour",
                "HonourX", "ModBrowser", "Engine", "FW3", "DiceSet_01", "DiceSet_02",
                "DiceSet_03", "DiceSet_04", "DiceSet_05", "DiceSet_06", "MainUI"}

# A write under one of these is a write into the GAME's own namespace, not the mod's.
BASE_NAMESPACES = ("public/shared/", "public/sharedev/", "public/shareddev/",
                   "public/gustav/", "public/gustavdev/", "public/gustavx/",
                   "public/game/", "public/engine/", "public/honour/",
                   "mods/shared/", "mods/gustav/", "mods/gustavdev/", "mods/gustavx/",
                   "mods/honour/", "game/gui/")


class AuditError(RuntimeError):
    """Something the audit needs is missing. Say what; never carry on with a guess."""


def find_divine() -> Path | None:
    for p in DIVINE_CANDIDATES:
        if p and p.is_file():
            return p
    which = shutil.which("Divine") or shutil.which("divine.exe")
    return Path(which) if which else None


# ------------------------------------------------------------- modsettings ---
def read_order(path: Path = MODSETTINGS) -> list[dict]:
    """The load order, in order. Each entry is whatever attributes it declares."""
    if not path.is_file():
        raise AuditError(
            f"no modsettings.lsx at {path}.\nEither BG3 has never been run with mods, "
            f"or the profile is somewhere else.")
    text = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for block in re.findall(r'<node id="ModuleShortDesc">(.*?)</node>', text, re.S):
        d = dict(re.findall(r'id="(\w+)"[^>]*value="([^"]*)"', block))
        if d.get("Name"):
            out.append(d)
    if not out:
        raise AuditError(f"{path} parsed but declares no mods. Refusing to report an "
                         f"empty load order as a clean one.")
    return out


# -------------------------------------------------------------------- paks ---
# ⭐ THE SPAWNS ARE THE COST, AND THEY PARALLELISE - MEASURED, session 73.
#   Item 87 proposed loading LSLib in-process (pythonnet) to kill the per-pak
#   `Divine.exe` spawn. Measured first, as the item required: pure process start is
#   48ms, a list-package is 71ms, so 68% of each call is spawn. Across 42 paks x ~3
#   calls that is ~6.0s of a 12.3s full audit - in-process would have bought ~2x.
#   ⚠ But the calls are INDEPENDENT, and threading the spawns we already make measured
#   6.4x at 8 workers and 9.0x at 16 (42 list-packages: 2.86s -> 0.45s -> 0.32s).
#   Better than the mined idea, with NO pythonnet, NO .NET runtime coupling and NO
#   second code path bound to an LSLib assembly version. Item 87 closed on this.
#   Threads, not processes, on purpose: the work is a blocking subprocess wait, so the
#   GIL is released the whole time and processes would only add their own spawn cost.
JOBS = 8          # measured sweet spot; 16 is faster still but saturates a small box


def _one_pak(divine: Path, pak: Path, work: Path, quick: bool) -> tuple:
    """Read ONE pak. Pure per-pak work, its own temp dir - safe to run concurrently."""
    rec = {"name": "", "uuid": "", "deps": [], "paths": [],
           "entries": [], "patches": [], "hashes": {},
           "goals": [], "flags": set()}
    listing = subprocess.run(
        [str(divine), "-g", "bg3", "-a", "list-package", "-s", str(pak)],
        capture_output=True, text=True, errors="replace", timeout=300)
    rec["paths"] = [ln.split("\t")[0].strip()
                    for ln in (listing.stdout or "").splitlines()
                    if ln.strip() and not ln.startswith(("Loading", "Listing"))]

    dest = work / pak.stem
    filt = "*meta.lsx" if quick else "*.lsx"
    subprocess.run([str(divine), "-g", "bg3", "-a", "extract-package",
                    "-s", str(pak), "-d", str(dest), "-x", filt],
                   capture_output=True, text=True, timeout=600, errors="replace")
    meta = next(dest.rglob("meta.lsx"), None)
    if meta:
        mt = meta.read_text(encoding="utf-8", errors="replace")
        i = mt.find('<node id="ModuleInfo"')
        head = mt[i:mt.find("<children>", i)] if i >= 0 else ""
        m = re.search(r'id="Name"[^>]*value="([^"]*)"', head)
        rec["name"] = m.group(1) if m else ""
        m = re.search(r'id="UUID"[^>]*value="([^"]*)"', head)
        rec["uuid"] = m.group(1) if m else ""
        dep_block = mt[:i] if i >= 0 else mt
        for blk in re.findall(r'<node id="ModuleShortDesc">(.*?)</node>',
                              dep_block, re.S):
            dm = re.search(r'id="Name"[^>]*value="([^"]*)"', blk)
            if dm and dm.group(1) not in BASE_MODULES:
                rec["deps"].append(dm.group(1))

    if not quick:
        subprocess.run([str(divine), "-g", "bg3", "-a", "extract-package",
                        "-s", str(pak), "-d", str(dest),
                        "-x", "*Stats/Generated/Data/*.txt"],
                       capture_output=True, text=True, timeout=600, errors="replace")
        for txt in dest.rglob("*.txt"):
            body = txt.read_text(encoding="utf-8", errors="replace")
            # `new entry "X"` … `using "X"` is a mod PATCHING an existing X.
            for blk in re.split(r'(?m)^new entry ', body)[1:]:
                nm = re.match(r'"([^"]+)"', blk)
                if not nm:
                    continue
                rec["entries"].append(nm.group(1))
                # ⭐ HASH THE BODY, not just the name. Two mods very often ship
                # the SAME definition - both derived from the same 5e source, or
                # one vendoring the other. Reporting that as a conflict is crying
                # wolf: whichever wins, the game gets identical data. Comments and
                # blank lines are stripped, because the question is what the ENGINE
                # sees, not how the file is formatted.
                lines = [ln.strip() for ln in blk.splitlines()
                         if ln.strip() and not ln.strip().startswith("//")]
                rec["hashes"][nm.group(1)] = hashlib.sha256(
                    chr(10).join(lines).encode()).hexdigest()
                um = re.search(r'(?m)^using "([^"]+)"', blk.split("new entry")[0])
                if um and um.group(1) == nm.group(1):
                    rec["patches"].append(nm.group(1))
        # ⭐ THE OSIRIS LAYER. Story mods conflict here, not in stats: a goal
        # file is the unit BG3 executes, and two mods writing the same one means
        # the loser is simply gone. Flags are the other real signal - two mods
        # writing the same named flag are talking about the same world state
        # whether they meant to or not.
        subprocess.run([str(divine), "-g", "bg3", "-a", "extract-package",
                        "-s", str(pak), "-d", str(dest),
                        "-x", "*Story/RawFiles/Goals/*"],
                       capture_output=True, text=True, timeout=600, errors="replace")
        for goal in dest.rglob("Story/RawFiles/Goals/*.txt"):
            rel = goal.relative_to(dest).as_posix()
            rec["goals"].append(rel)
            body = goal.read_text(encoding="utf-8", errors="replace")
            rec["flags"].update(
                re.findall(r"(?:Set|Clear)Flag\s*\(\s*([A-Za-z_]\w*)", body))

    return pak.name, rec


def pak_data(divine: Path, quick: bool, jobs: int = JOBS) -> dict:
    """{pak filename: {name, uuid, deps, paths, entries, patches}} read from the paks.

    ⚠ Order is restored after the parallel map. An audit whose output reshuffles
      between runs cannot be diffed, and a diff is how anyone uses this.
    """
    if not MODS_DIR.is_dir():
        raise AuditError(f"no Mods folder at {MODS_DIR}")
    paks = sorted(MODS_DIR.glob("*.pak"))
    work = Path(tempfile.mkdtemp(prefix="bg3_audit_"))
    try:
        if jobs > 1 and len(paks) > 1:
            with ThreadPoolExecutor(max_workers=jobs) as ex:
                pairs = list(ex.map(lambda pk: _one_pak(divine, pk, work, quick), paks))
        else:
            pairs = [_one_pak(divine, pk, work, quick) for pk in paks]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {name: rec for name, rec in sorted(pairs)}
    return out


def is_override(rec: dict) -> list[str]:
    """Paths this pak writes into a BASE-GAME namespace - the mark of an override."""
    return [p for p in rec["paths"] if p.lower().startswith(BASE_NAMESPACES)]


# ------------------------------------------------------------------ audit ---
def audit(order: list[dict], paks: dict) -> dict:
    listed_uuids = {e.get("UUID", "") for e in order if e.get("UUID")}
    listed_names = [e["Name"] for e in order]
    pos = {n: i for i, n in enumerate(listed_names)}
    by_name = {r["name"]: (pak, r) for pak, r in paks.items() if r["name"]}

    f = {"ghosts": [], "orphans": [], "overrides": [], "deps": [],
         "duplicates": [], "cycles": [],
         "path_conflicts": [], "entry_conflicts": [], "inheritance": [],
         "goal_conflicts": [], "story_overrides": [], "shared_flags": []}

    for e in order:
        n, u = e["Name"], e.get("UUID", "")
        if n in BASE_MODULES:
            continue
        if n not in by_name and u not in {r["uuid"] for r in paks.values()}:
            f["ghosts"].append({"name": n, "uuid": u})

    for pak, rec in paks.items():
        if rec["uuid"] and rec["uuid"] in listed_uuids:
            continue
        if rec["name"] and rec["name"] in pos:
            continue
        ov = is_override(rec)
        if ov:
            f["overrides"].append({"pak": pak, "name": rec["name"] or "(no meta.lsx)",
                                   "count": len(ov), "sample": sorted(ov)[:3]})
        else:
            f["orphans"].append({"pak": pak, "name": rec["name"] or "(no meta.lsx)",
                                 "files": len(rec["paths"])})

    for name, i in pos.items():
        rec = by_name.get(name, (None, None))[1]
        if not rec:
            continue
        for dep in rec["deps"]:
            if dep not in pos:
                f["deps"].append({"mod": name, "needs": dep, "problem": "not loaded"})
            elif pos[dep] > i:
                f["deps"].append({"mod": name, "needs": dep, "problem": "loads later",
                                  "mod_at": i + 1, "dep_at": pos[dep] + 1})

    # ⭐ DUPLICATES. A mod listed TWICE in modsettings is not cosmetic: `pos` above is
    #   built by enumeration, so a repeated name silently collapses to its LAST index
    #   and every ordering verdict in this file is then measured against a position the
    #   first copy does not have. Report it before anyone trusts those verdicts.
    #   Counted by NAME and by UUID separately - a mod can be duplicated under either.
    name_at = defaultdict(list)
    uuid_at = defaultdict(list)
    for i, e in enumerate(order):
        if e["Name"] in BASE_MODULES:
            continue
        name_at[e["Name"]].append(i + 1)
        if e.get("UUID"):
            uuid_at[e["UUID"]].append(i + 1)
    for nm, at in sorted(name_at.items()):
        if len(at) > 1:
            f["duplicates"].append({"kind": "name", "value": nm, "at": at})
    for uu, at in sorted(uuid_at.items()):
        if len(at) > 1:
            f["duplicates"].append({"kind": "uuid", "value": uu, "at": at})

    # ⭐ CYCLES. "loads later" above is a per-EDGE verdict, and a cycle is the one shape
    #   where every edge can be reported and NO order satisfies them all - so a reader
    #   fixing the edges one at a time chases their tail forever. Say so once, plainly.
    #   ⚠ A self-dependency (A needs A) is included on purpose: it is a real authoring
    #     slip, it is a cycle of length 1, and the per-edge check calls it merely "loads
    #     later" against itself, which reads as nonsense rather than as a fault.
    graph = {n: [d for d in (by_name.get(n, (None, {}))[1] or {}).get("deps", [])
                 if d in pos]
             for n in pos}
    found, colour = set(), {}
    def walk(node: str, stack: list) -> None:
        colour[node] = 1                       # grey: on the current path
        for nxt in sorted(graph.get(node, [])):
            if colour.get(nxt) == 1:           # back-edge closes a cycle
                cyc = stack[stack.index(nxt):] if nxt in stack else [nxt]
                k = min(range(len(cyc)), key=lambda j: cyc[j])
                rot = tuple(cyc[k:] + cyc[:k])  # rotate so one cycle reports once
                found.add(rot)
            elif colour.get(nxt) != 2:
                walk(nxt, stack + [nxt])
        colour[node] = 2                       # black: fully explored
    for n in sorted(pos):
        if colour.get(n) != 2:
            walk(n, [n])
    for cyc in sorted(found):
        f["cycles"].append({"mods": list(cyc), "self": len(cyc) == 1})

    active = {n: by_name[n][1] for n in pos if n in by_name}
    names = sorted(active)
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            na, nb = names[a], names[b]
            shared = ({p.lower() for p in active[na]["paths"]} &
                      {p.lower() for p in active[nb]["paths"]})
            if shared:
                first, second = (na, nb) if pos[na] < pos[nb] else (nb, na)
                f["path_conflicts"].append({"first": first, "second": second,
                                            "count": len(shared),
                                            "sample": sorted(shared)[:3],
                                            "winner": second})

    owner = defaultdict(list)
    # ⛔ SORTED, NOT SET ORDER. Python randomises string hashing per process, so
    #   iterating a set here made the finding order differ between two IDENTICAL
    #   serial runs - measured session 73, and it predates the parallel read.
    #   An audit nobody can diff against yesterday's is an audit nobody can use.
    for n, rec in active.items():
        for ent in sorted(set(rec["entries"])):
            owner[ent].append(n)
    for ent, mods in sorted(owner.items()):
        if len(mods) > 1:
            ordered = sorted(mods, key=lambda m: pos[m])
            hashes = {active[m]["hashes"].get(ent) for m in ordered}
            identical = len(hashes) == 1 and None not in hashes
            f["entry_conflicts"].append({"entry": ent, "mods": ordered,
                                         "winner": ordered[-1],
                                         "identical": identical})

    # ⭐ OSIRIS GOALS. Two mods writing the same goal file is a hard conflict - the
    # later one replaces the earlier wholesale, and the earlier mod's story simply does
    # not run. A goal written into a BASE-GAME module's folder is an override of the
    # game's own story.
    goal_owner = defaultdict(list)
    for n, rec in active.items():
        for g in rec["goals"]:
            goal_owner[g].append(n)
            parts = g.split("/")
            if len(parts) > 1 and parts[0] == "Mods" and parts[1] in BASE_MODULES:
                f["story_overrides"].append({"mod": n, "goal": g, "module": parts[1]})
    for g, mods in sorted(goal_owner.items()):
        if len(mods) > 1:
            ordered = sorted(mods, key=lambda m: pos[m])
            f["goal_conflicts"].append({"goal": g, "mods": ordered,
                                        "winner": ordered[-1]})

    # ⚠ SHARED EVENTS ARE DELIBERATELY NOT REPORTED. Half the installed mods react to
    # LevelLoaded, AddedTo and EntityEvent, because those fire constantly and Osiris
    # rules are ADDITIVE - every matching rule runs. Listing that would bury the two
    # checks above under a wall of normal behaviour. FLAGS are different: a named flag is
    # shared world state, so two mods writing one are coupled.
    flag_owner = defaultdict(list)
    for n, rec in active.items():
        for fl in sorted(rec["flags"]):
            flag_owner[fl].append(n)
    for fl, mods in sorted(flag_owner.items()):
        if len(mods) > 1:
            f["shared_flags"].append({"flag": fl, "mods": sorted(mods)})

    # ⭐ The inheritance rule. A self-`using` entry patches whoever else defines it.
    for n, rec in active.items():
        for ent in rec["patches"]:
            others = [m for m in owner.get(ent, []) if m != n]
            for other in others:
                ok = pos[n] > pos[other]
                f["inheritance"].append({
                    "patcher": n, "patches": other, "entry": ent, "ok": ok,
                    "patcher_at": pos[n] + 1, "other_at": pos[other] + 1})
    return f


def report(f: dict, order: list[dict], paks: dict) -> int:
    bad = 0
    print(f"\nload order: {len(order)} entries   installed paks: {len(paks)}\n")

    def head(t):
        print("=" * 74)
        print(t)
        print("=" * 74)

    head("GHOSTS - listed in modsettings, no pak on disk")
    for g in f["ghosts"]:
        print(f"  ERROR  {g['name']}  ({g['uuid']})")
        bad += 1
    print("  none\n" if not f["ghosts"] else "")

    head("OVERRIDES - not in modsettings, but they overwrite base-game files")
    for o in f["overrides"]:
        print(f"  {o['name']}  [{o['pak']}]")
        print(f"      {o['count']} file(s) into a base-game namespace, e.g.")
        for s in o["sample"]:
            print(f"        {s[:88]}")
    if f["overrides"]:
        print("\n  These are working as designed - an override is not registered, it\n"
              "  simply overwrites a path. ⚠ But precedence for an unlisted pak is NOT\n"
              "  guaranteed, and since Patch 8 shipped game paks can win against the user\n"
              "  Mods folder. If one is not taking effect, move it to the game install's\n"
              "  own Data folder.\n")
    else:
        print("  none\n")

    head("ORPHANS - not listed, and they override nothing. These do nothing at all")
    for o in f["orphans"]:
        print(f"  WARN   {o['name']}  [{o['pak']}]  {o['files']} file(s)")
    print("  none\n" if not f["orphans"] else "")

    head("DEPENDENCIES")
    for d in f["deps"]:
        where = (f" (#{d['mod_at']} needs #{d['dep_at']})" if "mod_at" in d else "")
        print(f"  ERROR  {d['mod']} needs '{d['needs']}' - {d['problem']}{where}")
        bad += 1
    print("  every declared dependency is present and loads first\n"
          if not f["deps"] else "")

    head("FILE CONFLICTS - same path written by two mods; the later one wins")
    for c in f["path_conflicts"]:
        print(f"  {c['first']}  vs  {c['second']}   ({c['count']} file(s))")
        for s in c["sample"]:
            print(f"      {s[:84]}")
        print(f"      -> '{c['winner']}' wins\n")
    print("  none\n" if not f["path_conflicts"] else "")

    head("STATS ENTRY CONFLICTS - same entry defined by two mods, different files")
    grouped = defaultdict(lambda: {"same": [], "diff": []})
    for c in f["entry_conflicts"]:
        grouped[tuple(c["mods"])]["same" if c["identical"] else "diff"].append(c["entry"])
    real = 0
    for mods, kinds in grouped.items():
        print(f"  {' vs '.join(mods)}")
        if kinds["diff"]:
            real += 1
            print(f"      {len(kinds['diff'])} entr(ies) DIFFER - the winner changes "
                  f"what the game loads:")
            for e in sorted(kinds["diff"])[:5]:
                print(f"        {e}")
            if len(kinds["diff"]) > 5:
                print(f"        ... and {len(kinds['diff']) - 5} more")
            print(f"      -> '{mods[-1]}' wins. Move the other below it to flip that.")
        if kinds["same"]:
            # ⭐ NOT A CONFLICT. Both mods define it byte-for-byte identically, so the
            # order between them decides nothing. Reporting it as a finding would train
            # someone to skim this section - and the section also carries the real ones.
            print(f"      {len(kinds['same'])} entr(ies) are IDENTICAL in both - "
                  f"order between these two decides nothing:")
            for e in sorted(kinds["same"])[:5]:
                print(f"        {e}")
            if len(kinds["same"]) > 5:
                print(f"        ... and {len(kinds['same']) - 5} more")
        print()
    if not f["entry_conflicts"]:
        print("  none\n")
    elif not real:
        print("  every shared entry is defined identically by both mods - nothing to "
              "decide.\n")

    head("STORY (Osiris) - goal files, and who overrides the base game's story")
    for c in f["goal_conflicts"]:
        print(f"  ERROR  {' vs '.join(c['mods'])} both write {c['goal']}")
        print(f"         '{c['winner']}' wins and the other's story does not run.")
        bad += 1
    for o in f["story_overrides"]:
        print(f"  note   {o['mod']} overrides base-game story in {o['module']}")
        print(f"         {o['goal']}")
    if not f["goal_conflicts"] and not f["story_overrides"]:
        print("  no LISTED mod writes another's goal file or overrides base-game story.")
        print("  (Override paks appear in their own section above - one of those may well")
        print("   be overwriting base-game story, which is exactly what it is for.)")
    print()

    head("DUPLICATES - the same mod listed more than once in modsettings")
    for d in f["duplicates"]:
        where = ", ".join(f"#{n}" for n in d["at"])
        print(f"  ERROR  {d['kind']} {d['value']} appears {len(d['at'])}x at {where}")
    print("  none\n" if not f["duplicates"] else
          "         ^ every ordering verdict above measures the LAST copy's position.\n"
          "           Remove the extras in the launcher, then re-run.\n")

    head("DEPENDENCY CYCLES - no load order can satisfy these")
    for c in f["cycles"]:
        if c["self"]:
            print(f"  ERROR  {c['mods'][0]} declares itself as its own dependency")
        else:
            print(f"  ERROR  {' -> '.join(c['mods'] + [c['mods'][0]])}")
    print("  none\n" if not f["cycles"] else
          "         ^ fixing these edge by edge cannot converge. One of the declared\n"
          "           dependencies is wrong and has to be dropped by its author.\n")

    head("SHARED FLAGS - two mods writing one named flag share world state")
    for s in f["shared_flags"]:
        print(f"  {s['flag']}: {', '.join(s['mods'])}")
    if not f["shared_flags"]:
        print("  none - no two mods write the same named flag")
    print("\n  (Mods reacting to the same EVENT is normal and not listed: Osiris rules\n"
          "  are additive, and everything hooks LevelLoaded.)\n")

    head("⭐ INHERITANCE ORDER - a self-`using` entry patches an existing definition")
    seen = set()
    for i in f["inheritance"]:
        key = (i["patcher"], i["patches"])
        if key in seen:
            continue
        seen.add(key)
        n = sum(1 for x in f["inheritance"]
                if (x["patcher"], x["patches"]) == key)
        if i["ok"]:
            print(f"  ok     {i['patcher']} (#{i['patcher_at']}) patches "
                  f"{i['patches']} (#{i['other_at']}) on {n} entr(ies) - "
                  f"correctly ordered after it")
        else:
            print(f"  ERROR  {i['patcher']} (#{i['patcher_at']}) patches "
                  f"{i['patches']} (#{i['other_at']}) on {n} entr(ies)")
            print(f"         but loads BEFORE it. The patch will resolve to nothing and "
                  f"then be overwritten.")
            print(f"         Move '{i['patcher']}' below '{i['patches']}'.")
            bad += 1
    print("  no mod patches another's entries\n" if not f["inheritance"] else "")

    print("=" * 74)
    if bad:
        print(f"{bad} problem(s) that change what the game loads.")
    else:
        print("No mod overwrites another's files or entries out of order.")
    # ⚠ Stated NARROWLY on purpose. The vague version of this line - "cannot see semantic
    # conflicts" - was used twice as a reason to flag a worry instead of measuring one.
    # Files, stats entries, goals and flags are all measured now, so what remains is small
    # and specific, and load order does not fix it anyway.
    print("⚠ WHAT IS STILL NOT MEASURED: two mods adding SEPARATE content to the same\n"
          "  moment - two camp scenes queued for one night, two encounters in one room.\n"
          "  Nothing is overwritten there, so no static check can see it, and REORDERING\n"
          "  DOES NOT HELP: both simply run. That is a design clash between the mods, and\n"
          "  the only tool for it is playing and watching.")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true",
                    help="skip stats extraction (no entry or inheritance checks)")
    ap.add_argument("--jobs", type=int, default=JOBS,
                    help=f"paks read in parallel (default {JOBS}; 1 = serial)")
    ap.add_argument("--json", action="store_true", help="machine-readable findings")
    a = ap.parse_args()

    try:
        order = read_order()
        divine = find_divine()
        if not divine:
            raise AuditError(
                "Divine.exe not found. Set BG3_DIVINE or install LSLib.\n"
                "Without it this can only read modsettings, which cannot tell you what "
                "any mod\nactually contains - and that is the entire point. Refusing to "
                "report a partial\naudit as a clean one.")
        paks = pak_data(divine, a.quick, max(1, a.jobs))
    except AuditError as e:
        print(f"cannot audit: {e}", file=sys.stderr)
        return 2

    f = audit(order, paks)
    if a.json:
        print(json.dumps({"order": [e["Name"] for e in order],
                          "findings": f}, indent=1, default=sorted))
        return 1 if (f["ghosts"] or f["deps"]
                     or any(not i["ok"] for i in f["inheritance"])) else 0
    return report(f, order, paks)


if __name__ == "__main__":
    sys.exit(main())
