"""
Audit the mod's VFX and SFX against the shipped game.

WHY THIS EXISTS. Nothing in this toolchain has ever looked at a single effect or
sound field. `validate.py` proves the stats parse and the icons resolve;
`loca_lint.py` proves the text is right. Both would pass a spell that casts in
total silence with no visual whatsoever, because a missing `CastSound` is not a
malformed file - it is a *quiet* one, and quiet is exactly what a static check
cannot notice.

There are two ways to get that wrong and this checks both:

  1. NAMING SOMETHING THAT DOES NOT EXIST. A sound event or an effect GUID that
     no vanilla file defines resolves to nothing at runtime. No error, no log
     line, just silence or an invisible cast. Every sound name and every effect
     GUID we reference is checked against the whole shipped corpus.

  2. NAMING NOTHING AT ALL. Harder to see, because `using` inheritance means a
     field you never wrote still has a value - just the wrong one. Warped Blade
     inherits `Target_Slash_New`, so it plays a mundane sword swing for what is
     supposed to be a Force cantrip. That is not a missing field; it is a field
     silently filled in with someone else's flavour.

MEASURED, NOT ASSERTED. The "you should have this field" list is not a wishlist -
it is what vanilla entries of the same SpellType actually carry, counted, and the
rate is printed with every finding so it can be judged rather than obeyed. A
field 4% of vanilla spells bother with is not a defect.

    py fx_audit.py              # audit, exit 1 on any ERROR
    py fx_audit.py --warn       # treat WARN as failure too
    py fx_audit.py --stats      # what vanilla carries, per SpellType, and nothing else
    py fx_audit.py --inherited  # also show fields we inherit rather than declare
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus_index as ci  # noqa: E402
import anim_textkeys as ak  # noqa: E402

# ⚠ A cp1252 console (Git Bash) cannot encode this file's own output, and the
# failure is a hard UnicodeEncodeError mid-print, not a mangled glyph. Enforced by
# tools/encoding_gate.py, because this exact bug was reintroduced three times in
# one day by people adding a warning sign to a warning message.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


MOD = ci.CFG.root
OURS = ci.CFG.stats
OUR_MEI = ci.CFG.public / "MultiEffectInfos"

# Fields that carry a sound EVENT name, and fields that carry an effect GUID.
# Split because they are checked against different corpora.
SOUND_FIELDS = ["PrepareSound", "PrepareLoopSound", "CastSound", "TargetSound",
                "VocalComponentSound", "SoundStart", "SoundLoop", "SoundStop",
                "SoundVocalStart", "SoundVocalEnd", "SpellSoundMagnitude"]
EFFECT_FIELDS = ["PrepareEffect", "CastEffect", "TargetEffect", "HitEffect",
                 "PositionEffect", "BeamEffect", "ApplyEffect", "StatusEffect",
                 "TargetHitEffect", "ImpactEffect", "DisappearEffect"]
ANIM_FIELDS = ["SpellAnimation", "DualWieldingSpellAnimation", "CastTextEvent",
               "SpellAnimationIntentType", "HitAnimationType"]

# SpellSoundMagnitude is an enum, not a sound event name. It lives in the sound
# family for reporting but must not be checked against the sound-event corpus.
NOT_A_SOUND_EVENT = {"SpellSoundMagnitude"}

GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Same shape, unanchored - for pulling a slot GUID out of a SpellAnimation entry,
# where each slot is "<guid>,,"  rather than a bare GUID.
GUID_ANY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


# ------------------------------------------------------------------ parsing --
def parse_stats(text: str) -> dict:
    """{name: {"type":..., "using":..., "data": {field: value}}} for one file."""
    out = {}
    for blk in re.split(r"(?=^new entry )", text, flags=re.M)[1:]:
        m = re.match(r'new entry "([^"]+)"', blk)
        if not m:
            continue
        data = dict(re.findall(r'^data "([^"]+)" "(.*)"$', blk, re.M))
        # `// FXMODEL: <vanilla entry>` names the vanilla spell this one is styled
        # after. It exists because a bare SpellType average is the wrong yardstick
        # for a spell with no `using`: Warp Assault has no TargetEffect, and
        # neither does Target_ShadowStrike_Instant, which is the thing it copies.
        # Without this the audit nags for a field its own model does not carry.
        model = re.search(r'^//\s*FXMODEL:\s*(\S+)', blk, re.M)
        typ = re.search(r'^type "([^"]+)"', blk, re.M)
        use = re.search(r'^using "([^"]+)"', blk, re.M)
        out[m.group(1)] = {"type": typ.group(1) if typ else "",
                           "using": use.group(1) if use else None,
                           "model": model.group(1) if model else None,
                           "data": data}
    return out


def load_vanilla() -> dict:
    entries = {}
    for root in ci.STATS_ROOTS:
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.txt")):
            try:
                entries.update(parse_stats(f.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                pass
    return entries


def load_ours() -> dict:
    ours = {}
    for f in sorted(OURS.glob("*.txt")):
        ours.update(parse_stats(f.read_text(encoding="utf-8")))
    return ours


def resolve(name, entries, field, _seen=None):
    """Walk the `using` chain for a field. Returns (value, defining_entry)."""
    _seen = _seen or set()
    while name and name in entries and name not in _seen:
        _seen.add(name)
        e = entries[name]
        if field in e["data"]:
            return e["data"][field], name
        name = e["using"]
    return None, None


def fx_model(name, entries, vanilla):
    """The vanilla entry this one is styled after, walking `using`.

    A child that `using`s one of OUR entries inherits its model too - Perfect
    Convergence is a Warp Assault, so Warp Assault's model is its model. Without
    the walk the chain dead-ends at a non-vanilla parent and every inherited spell
    gets nagged for a field its actual model never had.
    """
    seen = set()
    while name and name in entries and name not in seen:
        seen.add(name)
        e = entries[name]
        if e.get("model"):
            return e["model"]
        if e["using"] in vanilla:
            return e["using"]
        name = e["using"]
    return None


# ------------------------------------------------------- the vanilla corpus --
def sound_events(vanilla) -> set:
    ev = set()
    for e in vanilla.values():
        for f in SOUND_FIELDS:
            if f in NOT_A_SOUND_EVENT:
                continue
            v = e["data"].get(f, "")
            for part in v.split(";"):
                part = part.split(",")[0].strip()
                if part:
                    ev.add(part)
    return ev


def effect_guids(vanilla) -> set:
    """Every effect GUID vanilla references, plus every MultiEffectInfo it defines.

    Referenced-but-undefined is normal here: the LSX half of the corpus does not
    cover every pak (Gustav's MultiEffectInfos are not extracted), so a GUID that
    a shipped spell uses is proof enough that it exists.
    """
    g = set()
    for e in vanilla.values():
        for f in EFFECT_FIELDS:
            v = e["data"].get(f, "").strip()
            if GUID.match(v):
                g.add(v)
    try:
        lsx = ci.load_lsx()
        g |= {u.lower() for u in lsx.get("defs", {})}
    except Exception as e:
        # ⛔ NOT a silent pass. Every GUID this fails to load is one this tool will
        #   then report as MISSING - a swallowed load turns into a page of findings
        #   that are all false, which is worse than no run at all.
        print("WARN   the LSX index could not be loaded (%s) - GUID findings below "
              "may be FALSE. Re-run corpus_index.py." % type(e).__name__)
    for f in OUR_MEI.glob("*.lsx"):
        g.add(f.stem.lower())
    return g


def field_rates(vanilla) -> dict:
    """{(type, subtype): {field: (count_with, total)}} - what vanilla actually carries."""
    buckets = defaultdict(list)
    for name, e in vanilla.items():
        if e["type"] == "SpellData":
            st, _ = resolve(name, vanilla, "SpellType")
            buckets[("SpellData", st or "?")].append(name)
        elif e["type"] == "StatusData":
            st, _ = resolve(name, vanilla, "StatusType")
            buckets[("StatusData", st or "?")].append(name)
    rates = {}
    for key, names in buckets.items():
        n = len(names)
        c = Counter()
        for nm in names:
            for f in SOUND_FIELDS + EFFECT_FIELDS + ANIM_FIELDS:
                if resolve(nm, vanilla, f)[0]:
                    c[f] += 1
        rates[key] = {f: (c[f], n) for f in SOUND_FIELDS + EFFECT_FIELDS + ANIM_FIELDS}
    return rates


# ------------------------------------------------------------------ the run --

# ------------------------------------------------------- text-key checking --
ANIM_KEYS = MOD / "corpus" / "anim_textkeys.json"
# Ours first, then the shipped banks - a spell may point straight at a vanilla MEI.
# UNPACKED_LSX is imported rather than restated so there is one source of truth for
# where the converted game data lives.
MEI_DIRS = [OUR_MEI] + sorted(
    ak.UNPACKED_LSX.glob("*/Public/*/MultiEffectInfos"))


# ⚠ Every coverage number this tool prints is measured against SHIPPED clips only.
#   An animation replacer re-points the same slots at different clips, so the figure
#   describes an unmodded install and nothing else. Stated in the warning itself
#   rather than in a footnote, because the warning is what gets read.
SCOPE = "(Measured against VANILLA clips only - an animation replacer changes this.)"

# A key present on fewer than this fraction of a slot's playable-race clips fires for
# some characters and not others. 0.90 rather than 1.0 because a handful of clips
# genuinely lack even Footstep keys; anything below it is a real coverage hole.
KEY_COVERAGE_WARN = 0.90


def load_anim_keys():
    """{slot guid: {"clips": n, "keys": {key: clips_carrying_it}}} or None."""
    if not ANIM_KEYS.exists():
        return None
    return json.loads(ANIM_KEYS.read_text(encoding="utf-8"))["slots"]


def mei_text_keys(guid: str):
    """([(effect guid, field, key)], ours?) for one MultiEffectInfo. None if unreadable."""
    for d in MEI_DIRS:
        f = d / f"{guid}.lsx"
        if not f.exists():
            continue
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError:
            return []
        out = []
        for n in root.findall(".//node[@id='MultiEffectInfos']/children/node[@id='EffectInfo']"):
            at = {x.get("id"): x.get("value") for x in n.findall("attribute")}
            for field in ("StartTextKey", "EndTextKey"):
                if at.get(field):
                    out.append((at.get("EffectResourceGuid", "?")[:8], field, at[field]))
        return out, d == OUR_MEI
    return None          # MEI file not found anywhere we can read


def check_text_keys(ours, both, errors, warns, notes):
    """Do our effects fire on text keys the spell's own animation actually contains?

    THE BUG THIS EXISTS FOR. An EffectInfo fires on a named event baked into the
    animation clip. Name one the clip does not have and there is no error and no log
    line - the effect just does not fire on it, and if it also carries DetachSource /
    DetachTarget it spawns and is left behind wherever the caster was standing. Warp
    Assault shipped four such effects from 2026-08-09 to 2026-08-22 and it took three
    live reports to find, because every GUID and every name in the file was individually
    valid. What was wrong was the RELATIONSHIP between the MEI and the animation.

    A slot GUID is not one clip - it resolves to a different clip per weapon rig, which
    is why coverage is reported rather than a yes/no. c07a9d83 carries `Cast` on 190 of
    190 playable clips but `VFX_Power_Cast_01` on only 72: the piercing rigs play
    Precision_01_Attack, which uses VFX_Pierce_Cast_01 instead. A key at 38% is not a
    pass, it is a bug for most of the weapons in the game.
    """
    slots = load_anim_keys()
    if slots is None:
        notes.append("text keys unchecked - no corpus/anim_textkeys.json. "
                     "Run: py tools/anim_textkeys.py --rebuild")
        return

    for name in sorted(ours):
        if ours[name]["type"] != "SpellData":
            continue
        used = []
        for field in ("SpellAnimation", "DualWieldingSpellAnimation"):
            val, _ = resolve(name, both, field)
            if not val:
                continue
            for slot in val.split(";"):
                m = GUID_ANY.search(slot)
                if m:
                    used.append(m.group(0))
        if not used:
            continue

        missing_slot = [g for g in used if g not in slots]
        if missing_slot:
            errors.append(f"{name} uses animation slot {missing_slot[0]} which is not in "
                          f"corpus/anim_textkeys.json - the cache is stale. "
                          f"Run: py tools/anim_textkeys.py --rebuild")
            continue

        # Best coverage a SET of keys achieves in any one slot: the fraction of that
        # slot's clips carrying at least one of them.
        #
        # A set, not a single key, because a weapon-family fan-out is one effect spread
        # over several keys on purpose - Larian's own Smite_Thunderous_TargetEffect ships
        # every node twice, on VFX_Power_Hit_01 and VFX_Slash_Hit_01, because a rig's clip
        # carries exactly one of them. Graded per key that reads 78% and 14%; graded as
        # the set it actually is, it reads 78%. Scoring the parts separately would make
        # the tool cry wolf on correct code, and a check nobody believes is worse than no
        # check - which is roughly how the original dead-key bug survived four months.
        def coverage(keyset):
            best = 0.0
            for g in used:
                info = slots[g]
                total = info["clips"] or 1
                hit = sum(n for ks, n in info.get("keysets", [])
                          if keyset & set(ks))
                best = max(best, hit / total)
            return best

        for field in EFFECT_FIELDS:
            val, owner = resolve(name, both, field)
            if not val or not GUID.match(val.strip()):
                continue
            got = mei_text_keys(val.strip())
            if got is None:
                continue                      # MEI we cannot read at all
            keys, is_ours = got
            # Group by (effect resource, which key slot) - that is what a fan-out IS.
            fanout = defaultdict(set)
            for res, kind, key in keys:
                fanout[(res, kind)].add(key)
            for (res, kind), keyset in sorted(fanout.items()):
                cov = coverage(keyset) if keyset else None
                if cov == 0.0:
                    cov = None
                shown = ", ".join(sorted(keyset))
                where = (f"{name}.{field} [{res} {kind}={shown}"
                         + (f", {len(keyset)} keys]" if len(keyset) > 1 else "]"))

                # OWNERSHIP DECIDES SEVERITY, and this is the whole difference between a
                # finding and noise. A VANILLA MEI deliberately carries one node per
                # weapon family - VFX_Power_Cast_01, VFX_Pierce_Cast_01,
                # VFX_Slash_Cast_01 - and only the node matching the equipped weapon
                # fires. Dead keys there are Larian's fan-out working as designed, we
                # cannot fix them without editing their file, and the custom-patch
                # philosophy forbids that. So: report, do not fail.
                #
                # In an MEI WE SHIP, a dead key means we put someone else's effect
                # package on an animation that cannot fire it. That is the Warp Assault
                # bug exactly, and it is ours to fix.
                if not is_ours:
                    if cov is None:
                        notes.append(
                            f"{where} - dead on this spell's animation, but that MEI is "
                            f"vanilla and fans out over weapon families. Not ours to fix.")
                    continue
                # EVERY KEY IS STILL CHECKED INDIVIDUALLY FOR BEING DEAD. Grading the
                # group answers "will this fire", but it hides the thing this tool was
                # built for: a key that is in NO clip at all. Inside a fan-out with one
                # live sibling the group still scores 78%, so a dead key would sail
                # through - fault injection caught exactly that regression the moment
                # group grading went in.
                for one in sorted(keyset):
                    if coverage({one}) == 0.0:
                        errors.append(
                            f"{name}.{field} [{res} {kind}={one}] - that text key is in "
                            f"NONE of the clips this spell's animation plays. "
                            + ("The effect cannot fire on it."
                               if len(keyset) == 1 else
                               "Its sibling keys still fire, so the effect works - but "
                               "this node is dead weight or a typo."))
                if cov is None:
                    errors.append(
                        f"{where} - none of those text keys are in any clip this spell's "
                        f"animation plays. The effect cannot fire at all.")
                elif cov < KEY_COVERAGE_WARN:
                    # ⚠ NAME THE CORPUS. This read as a fact about the world for
                    #   weeks; it is a fact about an UNMODDED install, and the
                    #   difference was measured on 2026-08-29 (session 71). The mod.io
                    #   TwoHandedSwordAnimations replacer overrides 22 of the 22
                    #   animation subsets our spells name, across 17 body-type banks,
                    #   and ships zero animation assets - so it re-points them at other
                    #   vanilla clips. All 137 replacement resources resolved, and only
                    #   25 of them (18%) carry any of the text keys these warnings are
                    #   about. A percentage without its denominator is the same shape
                    #   as "0 conflicts" over 0 mods opened.
                    warns.append(
                        f"{where} - only {cov:.0%} of this spell's playable-race clips "
                        f"carry that key, so it fires for some weapons and not others. "
                        f"{SCOPE}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warn", action="store_true", help="treat WARN as failure")
    ap.add_argument("--stats", action="store_true", help="print vanilla rates and exit")
    ap.add_argument("--inherited", action="store_true",
                    help="also list fields we inherit rather than declare")
    ap.add_argument("--threshold", type=float, default=0.60,
                    help="flag a field this fraction of comparable vanilla entries carry")
    a = ap.parse_args()

    vanilla = load_vanilla()
    ours = load_ours()
    both = dict(vanilla)
    both.update(ours)          # our entries can `using` vanilla ones
    events = sound_events(vanilla)
    guids = effect_guids(vanilla)
    rates = field_rates(vanilla)

    if a.stats:
        for key in sorted(rates):
            fields = {f: v for f, v in rates[key].items() if v[0]}
            if not fields or key[1] == "?":
                continue
            print(f"\n{key[0]} / {key[1]}  ({list(fields.values())[0][1]} entries)")
            for f, (c, n) in sorted(fields.items(), key=lambda kv: -kv[1][0]):
                print(f"    {c/n:6.1%}  {f}  ({c}/{n})")
        return 0

    errors, warns, notes = [], [], []
    for name in sorted(ours):
        e = ours[name]
        if e["type"] not in ("SpellData", "StatusData", "InterruptData"):
            continue
        sub, _ = resolve(name, both, "SpellType" if e["type"] == "SpellData" else "StatusType")
        rate = rates.get((e["type"], sub or "?"), {})

        for f in SOUND_FIELDS + EFFECT_FIELDS:
            val, owner = resolve(name, both, f)
            declared = owner == name

            # (1) does what we NAME actually exist?
            if declared and val:
                if f in EFFECT_FIELDS and GUID.match(val.strip()):
                    if val.strip().lower() not in guids:
                        errors.append(f"{name}.{f} = {val} - no vanilla file defines or uses "
                                      f"that effect GUID, and we do not ship it. Invisible.")
                elif f in SOUND_FIELDS and f not in NOT_A_SOUND_EVENT:
                    for part in val.split(";"):
                        ev = part.split(",")[0].strip()
                        if ev and ev not in events:
                            errors.append(f"{name}.{f} = {ev} - no vanilla entry uses that "
                                          f"sound event. It will play nothing.")

            # (2) is it absent where vanilla of this shape almost always has one?
            c, n = rate.get(f, (0, 0))
            if not val and n >= 20 and c / n >= a.threshold:
                model = fx_model(name, both, vanilla)
                if model and not resolve(model, vanilla, f)[0]:
                    notes.append(f"{name}.{f} unset, and so is {model}'s - matching the "
                                 f"model beats matching the {sub} average ({c}/{n}).")
                else:
                    warns.append(f"{name}.{f} is unset - {c/n:.0%} of vanilla {sub} "
                                 f"{e['type']} carry it ({c}/{n})."
                                 + (f" Its model {model} does carry one." if model else ""))

            # (3) inherited from somewhere with different flavour
            if a.inherited and val and owner and owner != name:
                notes.append(f"{name}.{f} inherited from {owner} = {val[:60]}")

    # (3.5) do our effects fire on text keys the animation actually has?
    check_text_keys(ours, both, errors, warns, notes)

    # (4) an effect we SHIP that nothing points at.
    #
    # This check exists because of a specific 2026-08-19 failure. An edit script did
    # three PrepareEffect swaps, then hit an assertion on a fourth and threw - and
    # because it wrote the file only after all four succeeded, the three good edits
    # were discarded with the bad one. Three freshly authored MultiEffectInfos went
    # into the pak referenced by nothing, and every other check stayed green: the
    # OLD GUIDs were still valid vanilla ones, so nothing was missing, nothing was
    # malformed, and the mod looked clean while three spells silently kept the
    # vanilla effect they were supposed to have stopped using.
    referenced = set()
    for e in ours.values():
        for f in EFFECT_FIELDS:
            v = e["data"].get(f, "").strip().lower()
            if GUID.match(v):
                referenced.add(v)
    for f in sorted(OUR_MEI.glob("*.lsx")):
        if f.stem.lower() not in referenced:
            errors.append(f"{f.name} is shipped in the pak but NO stats entry references "
                          f"it. Either a spell was meant to point at it and does not, or "
                          f"it is dead weight in the archive.")

    for x in errors:
        print(f"ERROR  {x}")
    for x in warns:
        print(f"WARN   {x}")
    for x in notes:
        print(f"note   {x}")
    # \u2b50 COVERAGE BEFORE VERDICT.
    #
    # \u26a0 This printed "0 error(s), 0 warning(s) / clean - every sound and effect
    #   we name exists" and returned 0 when it had loaded NOTHING: no stats
    #   entries, no vanilla corpus, no text-key index. Demonstrated 2026-08-29
    #   against an empty mod tree. fx_audit is GATED IN build.ps1, so that is a
    #   build passing its effects check having checked nothing.
    #
    #   The corpus warnings were already printed - as `note` lines, beside a
    #   verdict that said clean. A note next to "clean" is read as "clean".
    #   Coverage has to be part of the verdict or it is not part of anything.
    print(f"\n{len(errors)} error(s), {len(warns)} warning(s)"
          + (f", {len(notes)} inherited" if a.inherited else ""))

    if not ours:
        print("\n\u26a0 NOTHING WAS AUDITED - 0 stats entries loaded from this mod. "
              "'0 errors' here\n  counts nothing, it does not clear anything. Check "
              "the mod tree and forge.json.")
        return 2
    if not vanilla:
        print("\n\u26a0 NO VANILLA CORPUS - every existence check silently passed "
              "because there was\n  nothing to check against. Run: py "
              "tools/corpus_index.py")
        return 2

    if not errors and not warns:
        print(f"clean - {len(ours)} of our entries checked against {len(vanilla):,} "
              f"vanilla; every sound\nand effect we name exists, and nothing "
              f"comparable vanilla always carries is missing.")
    return 1 if (errors or (a.warn and warns)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
