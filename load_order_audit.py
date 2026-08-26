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

⚠ ON OVERRIDES. A pak that is not in modsettings is treated as disabled - but one that
    overwrites vanilla file paths may still take effect, and its precedence is NOT
    guaranteed. Since Patch 8 the shipped game paks can win against anything in the user
    Mods folder; the reliable location for an override is the game install's own Data
    folder. Sources: NexusMods.App developer docs for BG3, and bg3modmanager.net.

⚠ WHAT IT CANNOT SEE. Semantic conflicts. Two camp mods can both hook the same event
    from different files with different entry names and fight at runtime, and nothing
    here will know. A clean run means "no mod overwrites another's files or entries",
    never "these mods are compatible".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
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
def pak_data(divine: Path, quick: bool) -> dict:
    """{pak filename: {name, uuid, deps, paths, entries, patches}} read from the paks."""
    if not MODS_DIR.is_dir():
        raise AuditError(f"no Mods folder at {MODS_DIR}")
    out = {}
    work = Path(tempfile.mkdtemp(prefix="bg3_audit_"))
    try:
        for pak in sorted(MODS_DIR.glob("*.pak")):
            rec = {"name": "", "uuid": "", "deps": [], "paths": [],
                   "entries": [], "patches": []}
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
                           capture_output=True, text=True, timeout=600)
            meta = next(dest.rglob("meta.lsx"), None)
            if meta:
                mt = meta.read_text(encoding="utf-8", errors="replace")
                i = mt.find('<node id="ModuleInfo"')
                head = mt[i:mt.find("<children>", i)] if i >= 0 else ""
                rec["name"] = (re.search(r'id="Name"[^>]*value="([^"]*)"', head) or
                               [None, ""])[1] if head else ""
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
                               capture_output=True, text=True, timeout=600)
                for txt in dest.rglob("*.txt"):
                    body = txt.read_text(encoding="utf-8", errors="replace")
                    # `new entry "X"` … `using "X"` is a mod PATCHING an existing X.
                    for blk in re.split(r'(?m)^new entry ', body)[1:]:
                        nm = re.match(r'"([^"]+)"', blk)
                        if not nm:
                            continue
                        rec["entries"].append(nm.group(1))
                        um = re.search(r'(?m)^using "([^"]+)"', blk.split("new entry")[0])
                        if um and um.group(1) == nm.group(1):
                            rec["patches"].append(nm.group(1))
            out[pak.name] = rec
    finally:
        shutil.rmtree(work, ignore_errors=True)
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
         "path_conflicts": [], "entry_conflicts": [], "inheritance": []}

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
    for n, rec in active.items():
        for ent in set(rec["entries"]):
            owner[ent].append(n)
    for ent, mods in owner.items():
        if len(mods) > 1:
            ordered = sorted(mods, key=lambda m: pos[m])
            f["entry_conflicts"].append({"entry": ent, "mods": ordered,
                                         "winner": ordered[-1]})

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
    grouped = defaultdict(list)
    for c in f["entry_conflicts"]:
        grouped[tuple(c["mods"])].append(c["entry"])
    for mods, entries in grouped.items():
        print(f"  {' vs '.join(mods)}   ({len(entries)} entr(ies))")
        for e in sorted(entries)[:5]:
            print(f"      {e}")
        if len(entries) > 5:
            print(f"      ... and {len(entries) - 5} more")
        print(f"      -> '{mods[-1]}' wins on all of them\n")
    print("  none\n" if not f["entry_conflicts"] else "")

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
    print("⚠ This cannot see SEMANTIC conflicts - two mods hooking the same event from\n"
          "  different entries will not appear here. A clean run is not a compatibility\n"
          "  guarantee.")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true",
                    help="skip stats extraction (no entry or inheritance checks)")
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
        paks = pak_data(divine, a.quick)
    except AuditError as e:
        print(f"cannot audit: {e}", file=sys.stderr)
        return 2

    f = audit(order, paks)
    if a.json:
        print(json.dumps({"order": [e["Name"] for e in order], "findings": f}, indent=1))
        return 1 if (f["ghosts"] or f["deps"]
                     or any(not i["ok"] for i in f["inheritance"])) else 0
    return report(f, order, paks)


if __name__ == "__main__":
    sys.exit(main())
