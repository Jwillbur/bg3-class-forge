"""
Probe another installed mod for real collisions with this one.

WHY THIS EXISTS. On 2026-08-19 the coexistence test (work-live Pass 8) was run
by hand against DEchoKnight: extract the pak, diff entry names, diff UUIDs, then
work out by inspection which of the shared GUIDs were actual collisions and which
were two mods pointing at the same vanilla asset. That last step is the whole
job. Six UUIDs appeared in both mods and every one of them was benign - four
animation clips, a character-creation pose, and the Fighter class ParentGuid that
every Fighter subclass is REQUIRED to carry.

So the useful question is never "do these mods share a GUID". It is:

    does either mod claim, as its own identity, something the other also claims?

A GUID is OWNED where it appears as a node's identity attribute (UUID, or the
node-specific MapKey/ID pairs corpus_index already establishes) and REFERENCED
everywhere else. Owned-vs-owned is a real conflict. Everything else is two mods
agreeing about vanilla, which is how mods are supposed to work.

    py conflict_probe.py                        # probe every other mod installed
    py conflict_probe.py <mod.pak|dir> [...]    # probe specific ones
    py conflict_probe.py --keep                 # keep the extracted copies
    py conflict_probe.py --verbose              # list benign shared references too
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus_index as ci  # noqa: E402

# --- mod config -----------------------------------------------------------
# Anchored on the MOD BEING AUDITED (the cwd), not on this file. Until
# 2026-09-06 every path below was the literal string CFG.name, so running
# this from another mod silently audited Warpblade instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402
CFG = modconfig.load(Path.cwd())


# ⚠ A cp1252 console (Git Bash) cannot encode this file's own output characters,
# and the failure is a hard UnicodeEncodeError mid-print, not a mangled glyph. It
# works in PowerShell (UTF-8) and dies in Git Bash, which is why it went unnoticed
# in seven tools until 2026-08-28 - and why it reappeared here on 2026-08-29 the
# moment a warning sign was added to an unguarded file.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


MOD = CFG.root
DIVINE = Path(r"C:\Modding\tools\lslib\Packed\Tools\Divine.exe")
GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                     r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
ENTRY_RE = re.compile(r'^new entry "([^"]+)"', re.M)
CONTENT_RE = re.compile(r'contentuid="([^"]+)"')

# Carried by every Fighter subclass by definition, and the key CF sorts on.
# Two mods sharing it is the system working, not a clash.
KNOWN_BENIGN = {
    "721dfac3-92d4-41f5-b773-b7072a86232f": "Fighter class ParentGuid - every "
                                            "Fighter subclass must carry it",
}

# Only these parts of the workspace ship. tools/ holds modsettings.known-good.lsx,
# which is a LOAD-ORDER TEMPLATE listing other mods' UUIDs - scanning it made us
# look like we owned CommunityLibrary's and CF's identities on the first run.
SHIPPED = ("Mods", "Public", "Localization")

# `ModuleShortDesc` is always a POINTER at a module - it is what meta.lsx uses to
# declare a dependency and what modsettings.lsx uses for load order. Only
# `ModuleInfo.UUID` is a mod's own identity. Without this, declaring a dependency
# on Compatibility Framework reads as claiming to BE Compatibility Framework.
NEVER_IDENTITY = {("ModuleShortDesc", "UUID")}


def mods_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Larian Studios/Baldur's Gate 3/Mods"


def extract(pak: Path, dest: Path) -> Path | None:
    if not DIVINE.is_file():
        print(f"  ! Divine.exe not at {DIVINE} - cannot read {pak.name}", file=sys.stderr)
        return None
    out = dest / pak.stem
    r = subprocess.run([str(DIVINE), "-g", "bg3", "-a", "extract-package",
                        "-s", str(pak), "-d", str(out)],
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        print(f"  ! extract failed for {pak.name}: "
              f"{(r.stderr or r.stdout).strip()[:200]}", file=sys.stderr)
        return None
    return out


def scan(root: Path, shipped_only: bool = False) -> dict:
    """Everything about a mod tree that could collide with another mod."""
    owned: dict[str, str] = {}          # guid -> "node_id/attr in file"
    referenced: set[str] = set()
    entries: dict[str, str] = {}        # stats entry name -> file
    handles: set[str] = set()
    text_files = 0

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if shipped_only:
            rel = path.relative_to(root).parts
            if not rel or rel[0] not in SHIPPED:
                continue
        suffix = path.suffix.lower()
        if suffix not in (".lsx", ".txt", ".xml", ".json"):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue

        if suffix == ".txt" and "Stats" in path.parts:
            text_files += 1
            for name in ENTRY_RE.findall(text):
                entries[name] = path.name

        if suffix == ".xml" and "Localization" in path.parts:
            handles.update(h.split(";")[0] for h in CONTENT_RE.findall(text))

        if suffix == ".lsx":
            # Same identity rule corpus_index uses, so "owned" means the same
            # thing here as it does everywhere else in this toolchain.
            for _region, node_id, attrs in ci.parse_lsx(text):
                for aid, (_atype, val) in attrs.items():
                    if not isinstance(val, str) or not GUID_RE.fullmatch(val):
                        continue
                    is_identity = ((aid == ci.IDENTITY_ATTR
                                    or (node_id, aid) in ci.IDENTITY_PAIRS)
                                   and (node_id, aid) not in NEVER_IDENTITY)
                    if is_identity:
                        owned.setdefault(val, f"{node_id}.{aid} in {path.name}")
                    else:
                        referenced.add(val)
        else:
            referenced.update(GUID_RE.findall(text))

    referenced -= set(owned)
    return {"owned": owned, "referenced": referenced, "entries": entries,
            "handles": handles, "stats_files": text_files, "root": root}


def report(mine: dict, theirs: dict, name: str, verbose: bool,
           vanilla_defs: set[str]) -> int:
    """Print one comparison. Returns the number of REAL conflicts."""
    print(f"\n=== {name} ===")
    print(f"    {len(theirs['entries'])} stats entries, "
          f"{len(theirs['owned'])} owned GUIDs, "
          f"{len(theirs['handles'])} loca handles")

    conflicts = 0

    # --- stats entry names: a genuine clash, later mod wins outright ---------
    shared = sorted(set(mine["entries"]) & set(theirs["entries"]))
    if shared:
        conflicts += len(shared)
        print(f"\n  CONFLICT  {len(shared)} shared stats entry name(s) - "
              f"whichever mod loads later wins, silently:")
        for n in shared[:20]:
            print(f"              {n}   (ours: {mine['entries'][n]}, "
                  f"theirs: {theirs['entries'][n]})")
    else:
        print("  ok        0 shared stats entry names")

    # --- owned identities: the real question --------------------------------
    # A GUID both mods "own" splits in two, and the split is the whole point.
    # If VANILLA already defines it, neither mod invented it - they are both
    # overriding a shipped node, which is normal and is what CF exists to
    # reconcile. If vanilla does NOT define it, two mods have independently
    # claimed the same new identity, and that is a genuine clash.
    both_own = [g for g in sorted(set(mine["owned"]) & set(theirs["owned"]))
                if g not in KNOWN_BENIGN]
    real = [g for g in both_own if g not in vanilla_defs]
    overrides = [g for g in both_own if g in vanilla_defs]

    if real:
        conflicts += len(real)
        print(f"\n  CONFLICT  {len(real)} GUID(s) claimed as a NEW identity by BOTH "
              f"(vanilla does not define these):")
        for g in real[:20]:
            print(f"              {g}")
            print(f"                ours:   {mine['owned'][g]}")
            print(f"                theirs: {theirs['owned'][g]}")
    else:
        print("  ok        0 owned-identity GUID collisions")

    if overrides:
        print(f"\n  note      {len(overrides)} vanilla node(s) BOTH mods override - "
              f"not a clash, but this is where CF earns its keep:")
        for g in overrides[:10]:
            print(f"              {g}  ({mine['owned'][g]})")

    # --- the case that made the Echo Knight test meaningful ------------------
    # The other mod OVERRIDES a shipped vanilla node that we also touch, whether
    # we override it or merely point at it. For two CF subclasses that node is
    # Fighter's L3 progression, and it is precisely the collision CF is there to
    # absorb - so it belongs in the report as a named, understood note rather
    # than being filtered away as "vanilla, therefore uninteresting".
    ours_touched = set(mine["owned"]) | mine["referenced"]
    contested = sorted((set(theirs["owned"]) & vanilla_defs) & ours_touched)
    if contested:
        print(f"\n  note      {len(contested)} vanilla node(s) THEY override that we "
              f"also touch:")
        for g in contested[:10]:
            how = "we override it too" if g in mine["owned"] else "we reference it"
            print(f"              {g}  ({theirs['owned'][g]}; {how})")
        print("              Two mods rewriting the same shipped node is the classic "
              "clash. Under\n              Compatibility Framework it is absorbed: "
              "CommitSubclasses() rebuilds the\n              list from every matching "
              "ClassDescription, so a static override is discarded\n              before "
              "it matters. Outside CF, this is where you would be patching.")

    # --- one mod owns what the other points at ------------------------------
    # Only interesting for identities the other mod actually INVENTED. Pointing
    # at a vanilla node both mods override is the case just above.
    they_ref_ours = sorted((set(mine["owned"]) & theirs["referenced"]) - vanilla_defs)
    we_ref_theirs = sorted((set(theirs["owned"]) & mine["referenced"]) - vanilla_defs)
    for label, guids, tbl in (("they reference OUR", they_ref_ours, mine["owned"]),
                              ("we reference THEIR", we_ref_theirs, theirs["owned"])):
        if guids:
            print(f"\n  note      {len(guids)} GUID(s) {label} own identities:")
            for g in guids[:10]:
                print(f"              {g}  ({tbl[g]})")

    # --- loca handles: shared handle = one mod's text overwrites the other's -
    shared_h = mine["handles"] & theirs["handles"]
    if shared_h:
        conflicts += len(shared_h)
        print(f"\n  CONFLICT  {len(shared_h)} shared loca handle(s) - "
              f"one mod's text will replace the other's:")
        for h in sorted(shared_h)[:10]:
            print(f"              {h}")
    else:
        print("  ok        0 shared loca handles")

    # --- benign agreement about vanilla -------------------------------------
    both_ref = mine["referenced"] & theirs["referenced"]
    benign = sorted(set(mine["owned"]) & set(theirs["owned"]) & set(KNOWN_BENIGN))
    print(f"  ok        {len(both_ref)} shared vanilla reference(s) - "
          f"both mods pointing at the same shipped asset, which is fine")
    for g in benign:
        print(f"  ok        {g}")
        print(f"              {KNOWN_BENIGN[g]}")
    if verbose and both_ref:
        for g in sorted(both_ref)[:40]:
            print(f"              {g}")

    return conflicts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="*",
                    help="mod .pak files or extracted directories; "
                         "default is every other mod in the game's Mods folder")
    ap.add_argument("--keep", action="store_true", help="keep extracted copies")
    ap.add_argument("--verbose", action="store_true",
                    help="list the benign shared vanilla references too")
    args = ap.parse_args()

    # Every GUID the shipped game defines. Lets the probe tell "both mods
    # override the same vanilla node" (normal) from "both mods invented the
    # same GUID" (a real clash) - without it, Expansion alone produced 73
    # noise lines on the first run.
    vanilla_defs: set[str] = set()
    try:
        vanilla_defs = set(ci.load_lsx().get("defs", {}))
        print(f"{len(vanilla_defs):,} vanilla-defined GUIDs loaded for classification")
    except Exception as exc:                       # noqa: BLE001
        print(f"! no LSX index ({exc}); every shared owned GUID will read as a "
              f"conflict. Run corpus_index.py first.", file=sys.stderr)

    mine = scan(MOD, shipped_only=True)
    print(f"{CFG.name}: {len(mine['entries'])} stats entries, "
          f"{len(mine['owned'])} owned GUIDs, {len(mine['handles'])} loca handles")

    targets: list[Path] = [Path(t) for t in args.targets]
    if not targets:
        d = mods_dir()
        if not d.is_dir():
            print(f"no Mods folder at {d}", file=sys.stderr)
            return 2
        targets = [p for p in sorted(d.glob("*.pak")) if p.stem != CFG.name]
        if not targets:
            print("no other mods installed - nothing to probe against")
            return 0

    tmp = Path(tempfile.mkdtemp(prefix="conflict_probe_"))
    total = 0
    probed = 0
    skipped: list[str] = []
    empty: list[str] = []
    asked = len(targets)
    try:
        for t in targets:
            if t.is_dir():
                root, label = t, t.name
            elif t.suffix.lower() == ".pak":
                root, label = extract(t, tmp), t.stem
                if root is None:
                    skipped.append(t.name)
                    continue
            else:
                print(f"  ! skipping {t} - not a .pak or a directory", file=sys.stderr)
                continue
            # ⚠ AN EMPTY EXTRACTION IS NOT A CLEAN RESULT.
            #   A corrupt pak extracts to nothing, and nothing shares no stats
            #   entries with us - so it was counted as PROBED and reported clean.
            #   That is the fail-open bug one level down: not 'we could not read
            #   it' but 'we read it and it was empty', which look identical in a
            #   conflict count. Recorded separately so the difference is visible.
            #   NOT an error and NOT in the exit code: asset-only mods legitimately
            #   ship zero stats entries, and a gate that reddens on those gets
            #   switched off within a week.
            theirs = scan(root)
            if not (theirs["entries"] or theirs["owned"] or theirs["handles"]):
                empty.append(label)
            probed += 1
            total += report(mine, theirs, label, args.verbose, vanilla_defs)
    finally:
        if args.keep:
            print(f"\nextracted copies kept in {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    # ⭐ COVERAGE IS PART OF THE VERDICT, NOT A FOOTNOTE.
    #
    # ⚠ This used to print "{probed} mod(s) probed, 0 real conflict(s)" and
    #   return 0 whatever `probed` was. A pak that would not extract - Divine.exe
    #   missing, a corrupt file - was `continue`d past with one line on STDERR,
    #   so with Divine absent EVERY target was skipped and the tool printed
    #   "0 mod(s) probed, 0 real conflict(s)" and exited 0. A clean bill of health
    #   over zero work. Demonstrated 2026-08-29 against an unextractable pak.
    #
    #   That matters more here than in most tools, because this one's output has
    #   been QUOTED AS EVIDENCE: a session handover states "conflict_probe.py
    #   cleared all 41 installed mods". That sentence is only worth anything if
    #   41 were actually opened, and until now nothing made the difference visible
    #   in the verdict or the exit code.
    print(f"\n{probed} of {asked} mod(s) probed, {total} real conflict(s).")
    if skipped:
        print(f"\n⚠ {len(skipped)} TARGET(S) COULD NOT BE READ and were NOT checked: "
              + ", ".join(skipped[:8]) + ("..." if len(skipped) > 8 else ""))
        print("  This is NOT a clean result for them - it is no result. Usually a "
              "missing Divine.exe;")
        print(f"  expected at {DIVINE}")
    if empty:
        print(f"\n  note      {len(empty)} probed mod(s) contained NO stats entries, "
              f"owned GUIDs or loca\n            handles: "
              + ", ".join(empty[:8]) + ("..." if len(empty) > 8 else ""))
        print("            Nothing to collide with, so '0 conflicts' for those is "
              "arithmetic,\n            not evidence. Normal for asset-only mods; "
              "suspicious for a stats mod.")
    if probed == 0 and asked:
        print("\n⚠ NOTHING WAS PROBED. '0 conflicts' here means 0 mods were opened, "
              "not that the\n  mod is clean. Fix the extraction before believing "
              "any part of this run.")
        return 2
    if not total and not skipped:
        print("Shared GUIDs that are references rather than identities are not "
              "conflicts - two mods pointing at the same vanilla asset is how "
              "this is meant to work.")
    # A partial run is not a pass. Callers gate on the exit code, and a gate that
    # says OK over unread targets is the thing this whole comment is about.
    if total:
        return 1
    return 3 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
