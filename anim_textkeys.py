#!/usr/bin/env python3
"""Index the text keys carried by the animation clips our spells actually play.

    py tools/anim_textkeys.py --rebuild     # scan the banks, write the cache
    py tools/anim_textkeys.py               # show what the cache holds

WHY THIS EXISTS
---------------
An `EffectInfo` fires on a `StartTextKey` - a named event baked into the animation
clip. If the clip does not contain that name, the effect does not fire on it. There is
no error, no log line: the effect either never plays or plays at some other moment, and
if it also carries `DetachSource`/`DetachTarget` it spawns and is left behind wherever
the caster happened to be. That is a silent, invisible-until-playtest failure, and it
cost three separate live reports on Warp Assault before it was found.

The specific case: Warp Assault borrowed Shadow Strike's whole effect package, four of
whose children key off `VFX_Antic_02` / `VFX_Antic_03` / `VFX_Cast_03`. Those keys live
in Shadow Strike's Monk ANTIC clip. Iteration 2 deliberately emptied the antic slot and
put `CMBT_Skill_Power_01_Attack` in the cast slot - which carries none of those names.
Four effects were keyed to events that could never fire, from 2026-08-09 until 2026-08-22.

Nothing in the toolchain could have caught it, because every one of those GUIDs and
names is individually valid. What was wrong is the RELATIONSHIP between the MEI and the
animation, and that needs the clips' real key lists to check.

HOW THE LOOKUP WORKS
--------------------
`SpellAnimation` holds nine slots of animation-slot GUIDs. A slot GUID is not a clip -
it is a key into each character's animation set, so one slot resolves to many real clips
(male/female, every body type, every weapon rig). In an animation bank those appear as:

    <node id="Object">
      <attribute id="ID"     value="<real animation resource guid>"/>
      <attribute id="MapKey" value="<slot guid from SpellAnimation>"/>

and the resource with that ID carries its keys as `Events` children:

    <node id="Resource">
      <attribute id="ID"   value="<real animation resource guid>"/>
      <attribute id="Name" value="HUM_M_Rig_1HS_CMBT_Skill_Power_01_Attack"/>
      <children><node id="Events">
        <attribute id="ID"   value="Cast"/>
        <attribute id="Time" value="0.192"/>

Both live in the same `_merged.lsx`, so one pass per file resolves it.

WHAT THE CACHE STORES, AND WHY IT IS NOT THE WHOLE GAME
-------------------------------------------------------
Only the slot GUIDs our own spells reference. The full index would be tens of thousands
of slots across 1475 files and 0.7 GB of XML; ours is a couple of dozen. `fx_audit`
ERRORS if it meets a slot GUID the cache does not hold, so a stale cache announces
itself instead of quietly passing - which matters, since the whole point is catching a
silent failure.

A key is recorded as present if ANY clip behind the slot carries it, and the per-clip
counts are kept so a key that exists on only some rigs can be seen for what it is: a
two-handed-only key on a spell any weapon can cast is a real hazard, not a pass.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus_index as ci  # noqa: E402
import modconfig  # noqa: E402

# Anchored on the MOD being audited, not on this file. The tool lives in
# forge/ now and is shared, so `parent.parent` would have pointed at forge.
CFG = modconfig.load(Path.cwd())
MOD = CFG.root
CACHE = MOD / "corpus" / "anim_textkeys.json"
OURS = CFG.stats

# The converted-to-lsx unpack. corpus_index.py points at the same pair of roots.
UNPACKED_LSX = Path(r"C:\Modding\bg3_unpacked_lsx")
# PLAYABLE RACES ONLY, and this is the difference between a check that means something
# and one that does not. An animation-slot GUID is a key into EVERY creature's animation
# set, so an unfiltered scan of slot 32c33123 returns 203 clips of which nearly all are
# badgers, cloakers and flesh golems. A union over that set passes any key some monster
# happens to carry, and the per-clip counts become noise. This is a Fighter subclass:
# the only rigs that can ever play its animations are the ten playable races.
PLAYABLE = ("Dragonborn", "Dwarves", "Elves", "Githyanki", "Gnomes",
            "HalfElves", "HalfOrcs", "Halflings", "Humans", "Tieflings")
BANKS = "*/Public/*/Content/Assets/Characters/{race}/**/_merged.lsx"

ANIM_FIELDS = ("SpellAnimation", "DualWieldingSpellAnimation")
GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

RE_OBJECT = re.compile(
    r'<node id="Object">\s*'
    r'<attribute id="ID" type="FixedString" value="([0-9a-f-]{36})"\s*/>\s*'
    r'<attribute id="MapKey" type="FixedString" value="([0-9a-f-]{36})"\s*/>')
RE_RESOURCE = re.compile(r'<node id="Resource">')
RE_RES_ID = re.compile(r'<attribute id="ID" type="FixedString" value="([0-9a-f-]{36})"\s*/>')
RE_RES_NAME = re.compile(r'<attribute id="Name" type="LSString" value="([^"]*)"\s*/>')
RE_EVENT = re.compile(
    r'<node id="Events">\s*<attribute id="ID" type="FixedString" value="([^"]*)"')


def _parse(text: str) -> dict:
    """{entry: {"using":..., "data": {field: value}}} - just enough for animations."""
    out = {}
    for blk in re.split(r"(?=^new entry )", text, flags=re.M)[1:]:
        m = re.match(r'new entry "([^"]+)"', blk)
        if not m:
            continue
        use = re.search(r'^using "([^"]+)"', blk, re.M)
        out[m.group(1)] = {
            "using": use.group(1) if use else None,
            "data": dict(re.findall(r'^data "([^"]+)" "(.*)"$', blk, re.M)),
        }
    return out


def our_slot_guids() -> dict:
    """{slot guid: [ "SpellName field slot N", ... ]} for every animation slot we play.

    INHERITANCE IS RESOLVED, and it has to be. Warped Blade declares no SpellAnimation
    of its own - it inherits Target_Slash_New's - so a scan of only our own files misses
    every clip it actually plays. The first version of this did exactly that, and
    fx_audit caught it as a stale-cache error on Warped Blade. Vanilla entries are read
    through corpus_index's own stats roots, the same source the rest of the audit uses.
    """
    entries = {}
    for root in ci.STATS_ROOTS:
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.txt")):
            try:
                entries.update(_parse(f.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                pass
    ours = {}
    for f in sorted(OURS.glob("*.txt")):
        ours.update(_parse(f.read_text(encoding="utf-8")))
    entries.update(ours)

    def resolve(name, field):
        seen = set()
        while name and name in entries and name not in seen:
            seen.add(name)
            if field in entries[name]["data"]:
                return entries[name]["data"][field]
            name = entries[name]["using"]
        return None

    used = defaultdict(list)
    for name in ours:
        for field in ANIM_FIELDS:
            val = resolve(name, field)
            if not val:
                continue
            for i, slot in enumerate(val.split(";")):
                g = GUID.search(slot)
                if g:
                    used[g.group(0)].append(f"{name} {field} slot {i}")
    return dict(used)


def scan(wanted: set) -> dict:
    """One pass over the animation banks. {slot guid: {clip name: [keys]}}."""
    out = defaultdict(dict)
    files = sorted(f for race in PLAYABLE
                   for f in UNPACKED_LSX.glob(BANKS.format(race=race)))
    if not files:
        print(f"ERROR no animation banks under {UNPACKED_LSX}", file=sys.stderr)
        return {}
    t0 = time.time()
    for n, f in enumerate(files, 1):
        if n % 200 == 0:
            print(f"  {n}/{len(files)} files, {time.time()-t0:.0f}s", file=sys.stderr)
        try:
            s = io.open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        # Which real animation resources do the slots we care about map to, here?
        want_res = {}
        for res_id, map_key in RE_OBJECT.findall(s):
            if map_key in wanted:
                want_res.setdefault(res_id, set()).add(map_key)
        if not want_res:
            continue
        # Resource blocks are flat siblings, so splitting on the opening tag is enough
        # to bound each one - there is no nested Resource to confuse the split.
        for seg in RE_RESOURCE.split(s)[1:]:
            rid = RE_RES_ID.search(seg)
            if not rid or rid.group(1) not in want_res:
                continue
            nm = RE_RES_NAME.search(seg)
            clip = nm.group(1) if nm else rid.group(1)
            keys = sorted(set(RE_EVENT.findall(seg)))
            for slot in want_res[rid.group(1)]:
                out[slot][clip] = keys
    print(f"  scanned {len(files)} files in {time.time()-t0:.0f}s", file=sys.stderr)
    return dict(out)


def rebuild() -> int:
    used = our_slot_guids()
    print(f"{len(used)} distinct animation slots referenced by our spells")
    data = scan(set(used))
    payload = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
               "source": str(UNPACKED_LSX), "slots": {}}
    for slot, refs in sorted(used.items()):
        clips = data.get(slot, {})
        counts = defaultdict(int)
        for keys in clips.values():
            for k in keys:
                counts[k] += 1
        # Unique key SETS with a count each, not just per-key totals. Per-key numbers
        # cannot answer the question a weapon-family fan-out actually poses - "how many
        # clips carry AT LEAST ONE of these keys" - and summing them would be wrong
        # whenever two keys co-occur. Storing the distinct sets keeps that exact, and
        # compresses well because most clips share a set.
        sets = defaultdict(int)
        for keys in clips.values():
            sets[tuple(sorted(keys))] += 1
        payload["slots"][slot] = {
            "used_by": sorted(refs),
            "clips": len(clips),
            "sample_clip": sorted(clips)[0] if clips else None,
            # key -> how many of this slot's clips carry it
            "keys": {k: counts[k] for k in sorted(counts)},
            "keysets": [[list(k), n] for k, n in sorted(sets.items(), key=lambda kv: -kv[1])],
        }
        if not clips:
            print(f"  WARNING {slot} resolved to NO clips ({', '.join(sorted(refs))})")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"wrote {CACHE}  ({CACHE.stat().st_size/1024:.0f} KB)")
    return 0


def show() -> int:
    if not CACHE.exists():
        print(f"no cache at {CACHE} - run with --rebuild", file=sys.stderr)
        return 1
    d = json.loads(CACHE.read_text(encoding="utf-8"))
    print(f"generated {d['generated']}  |  {len(d['slots'])} slots\n")
    for slot, info in d["slots"].items():
        print(f"{slot}  {info['clips']} clips  e.g. {info['sample_clip']}")
        print(f"    used by : {', '.join(info['used_by'])}")
        print(f"    keys    : {', '.join(f'{k}({v})' for k, v in info['keys'].items()) or '(none)'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    return rebuild() if a.rebuild else show()


if __name__ == "__main__":
    sys.exit(main())
