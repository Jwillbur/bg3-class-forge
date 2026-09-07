# SPDX-License-Identifier: GPL-3.0-or-later
"""Check what the mod TELLS the player against what it actually DOES.

    py tools/tooltip_audit.py            # every finding
    py tools/tooltip_audit.py --strict   # exit 1 on WARN as well as ERROR
    py tools/tooltip_audit.py --json     # machine-readable

⭐ WHY THIS EXISTS
    Every other check in this toolkit asks whether the mod WORKS. None of them asks
    whether it is HONEST. Those are different questions and only one of them had a tool.

    A tooltip is not decoration - it is the only contract the player gets. `validate.py`
    is perfectly happy with a spell whose tooltip promises a status the functors never
    apply, because both halves are individually well-formed. The player finds out by
    counting turns and concluding the mod is broken, which is the worst way for anyone
    to learn anything.

⛔ THE CASE THAT PROVED IT, found on the first run (2026-08-31)
    Warp Assault's `TooltipStatusApply` says 2 turns of Spatial Debt. With the
    **Accelerated Debt** Technique taken it applies 3, and Perfect Convergence's says 4
    while it applies 6. The tooltip is not wrong exactly - it is wrong for the players
    who took the Technique, which is the shape of a bug you cannot see by reading either
    file on its own. BG3 cannot branch a `TooltipStatusApply`, but a passive CAN carry
    `UnlockSpellVariant(... ModifyTooltipDescription(...))`, so this is fixable rather
    than merely regrettable.

⚠ WHAT IT IS NOT
    It cannot read the localisation prose. If the DESCRIPTION text says "two turns" and
    the functors say three, only a human catches that - this tool compares the STRUCTURED
    tooltip fields, which are the machine-readable half of the same promise. It reports
    the prose handles beside each finding so the check is at least cheap to finish by eye.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sim as S  # noqa: E402  - the paren-aware functor splitter lives there

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Where a spell's real effects can live. ⚠ SpellProperties matters as much as
# SpellSuccess and leaving it out is not a small omission: a Shout applies everything
# from SpellProperties, so a first draft of this audit reported both of the mod's Shouts
# as promising a status they never applied. Both were correct; the audit was not.
EFFECT_FIELDS = ("SpellSuccess", "SpellProperties", "StatsFunctors", "SpellFail")

# A status the player is never shown cannot be missing from a tooltip.
HIDDEN_FLAGS = ("DisableOverhead", "DisableCombatlog", "DisablePortraitIndicator")


# ⚠ DIVERGENCES A DIFFERENT FEATURE ALREADY EXPLAINS.
#
# BG3 cannot branch a `TooltipStatusApply`, and it does not need to: the convention the
# base game follows everywhere is that a spell's tooltip states the BASE case and the
# feature that modifies it explains its own modification. Battle Master works this way,
# and so does every conditional passive in the game. Flagging that shape forever would
# make this tool noise, and noise is how a checker gets ignored - see mindok
# `guard-that-cries-wolf`.
#
# ⛔ EACH ENTRY MUST NAME THE FEATURE THAT DOES THE EXPLAINING, and a control asserts
#   that feature actually exists and that its description really mentions the mechanic.
#   An acceptance that cannot be checked is just a silenced warning.
EXPLAINED_BY = {
    ("Target_Warpblade_WarpAssault", "conditional-divergence"):
        "Warpblade_Technique_AcceleratedDebt",
    ("Target_Warpblade_WarpedBlade", "conditional-divergence"):
        "Warpblade_Technique_AcceleratedDebt",
    ("Target_Warpblade_PerfectConvergence", "conditional-divergence"):
        "Warpblade_Technique_AcceleratedDebt",
    ("Target_Warpblade_WarpAssault", "delivered-not-promised"):
        "Warpblade_SpatialAnchor",
    ("Target_Warpblade_PerfectConvergence", "delivered-not-promised"):
        "Warpblade_SpatialAnchor",
}

def split_args(inner: str) -> list[str]:
    """Split a functor's arguments on top-level commas only.

    ⛔ A naive `.split(",")` cuts `max(1,MainMeleeWeapon)` in half, and the first run of
      this tool did exactly that - it reported Warp Assault and Perfect Convergence as
      promising weapon damage that "nothing deals", when both deal it. A checker's own
      false ERROR is worse than the bug it was hunting: it sends someone to fix working
      code. Same lesson as sim.py's functor splitter, one layer down.
    """
    out, depth, cur = [], 0, []
    for ch in inner:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def statuses(blob: str) -> list[tuple[str, str | None, str]]:
    """[(status, duration or None, the condition it sits behind)] from a functor list."""
    out = []
    for f in S.split_functors(blob or ""):
        cond, call = S.parse_functor(f)
        i = 0
        while True:
            i = call.find("ApplyStatus(", i)
            if i < 0:
                break
            j, depth = i + len("ApplyStatus("), 1
            while j < len(call) and depth:
                if call[j] == "(":
                    depth += 1
                elif call[j] == ")":
                    depth -= 1
                j += 1
            args = split_args(call[i + len("ApplyStatus("):j - 1])
            i = j
            if args and args[0] in ("SELF", "SWAP", "OBSERVER_SOURCE", "OBSERVER_TARGET"):
                args = args[1:]
            if not args:
                continue
            dur = args[2] if len(args) >= 3 else None
            out.append((args[0], dur, cond or ""))
    return out


def damages(blob: str) -> list[str]:
    """Damage a tooltip should describe: what lands on the TARGET.

    ⚠ `GROUND:`-prefixed functors are excluded, and that is not a convenience. On an
      attack spell `SpellProperties` is the miss/ground behaviour, so counting it made
      this tool report Warped Blade as dealing 3 instances against a 2-entry tooltip -
      a tooltip that is in fact correct, because you do not advertise your miss.
    """
    out = []
    for f in S.split_functors(blob or ""):
        _cond, call = S.parse_functor(f)
        if re.match(r"^(GROUND|AOE|AI_ONLY|AI_IGNORE)\s*:", call.strip()):
            continue
        # ⚠ `[^)]*` stops at the FIRST close-paren, which truncates
        #   DealDamage(max(1,MainMeleeWeapon), ...) to "max(1". Scan with a depth
        #   counter instead - the same mistake the arg splitter made.
        i = 0
        while True:
            i = call.find("DealDamage(", i)
            if i < 0:
                break
            j, depth = i + len("DealDamage("), 1
            while j < len(call) and depth:
                if call[j] == "(":
                    depth += 1
                elif call[j] == ")":
                    depth -= 1
                j += 1
            out.append(call[i + len("DealDamage("):j - 1])
            i = j
    return out


def norm_dmg(arg: str) -> str:
    """Compare a damage expression by its shape, not its punctuation.

    `max(1,MainMeleeWeapon)` and `MainMeleeWeapon` are the same promise to a reader, and
    the trailing flags on `DealDamage(X,Force,Magical,,0,,true,true)` are delivery
    details. Normalising here is what keeps this tool from crying wolf on every entry.
    """
    a = split_args(arg)
    amount = a[0].strip() if a else ""
    dtype = a[1].strip() if len(a) > 1 else ""
    amount = re.sub(r"^max\(\s*\d+\s*,\s*(.*?)\s*\)$", r"\1", amount)
    return f"{amount}|{dtype}"


def audit(stats: dict) -> list[dict]:
    out: list[dict] = []

    def add(sev, entry, what, detail):
        out.append({"severity": sev, "entry": entry, "check": what, "detail": detail})

    for name, e in sorted(stats.items()):
        effects = " ; ".join(e.get(f, "") for f in EFFECT_FIELDS if e.get(f))
        if not effects:
            continue
        applied = statuses(effects)
        applied_names = {s for s, _d, _c in applied}

        # ---- A. promised but never applied -------------------------------------
        tip = e.get("TooltipStatusApply")
        if tip:
            for s, dur, _c in statuses(tip):
                if s not in applied_names:
                    add("ERROR", name, "promised-not-delivered",
                        f"tooltip promises {s} and no functor applies it")
                    continue
                # ---- C. true only on one branch --------------------------------
                real = [(d, c) for st_, d, c in applied if st_ == s]
                unconditional = [d for d, c in real if not c]
                conditional = [(d, c) for d, c in real if c]
                if dur is not None and conditional and not unconditional:
                    vals = sorted({d for d, _ in conditional if d})
                    if len(vals) > 1 or (vals and vals[0] != dur):
                        add("WARN", name, "conditional-divergence",
                            f"tooltip says {s} for {dur}, but the real values are "
                            f"{'/'.join(vals)} depending on a condition")

        # ---- B. applied to the target but never advertised ----------------------
        if tip is not None:
            promised = {s for s, _d, _c in statuses(tip)}
            for s, _d, cond in applied:
                if s in promised or "SELF" in cond:
                    continue
                st_entry = stats.get(s, {})
                flags = st_entry.get("StatusPropertyFlags", "")
                if any(h in flags for h in HIDDEN_FLAGS):
                    continue                      # invisible to the player by design
                if s not in stats:
                    continue                      # vanilla status; not ours to advertise
                add("WARN", name, "delivered-not-promised",
                    f"applies {s} to the target and the tooltip does not mention it")

        # ---- D. the damage list disagrees with the damage ------------------------
        tdl = e.get("TooltipDamageList")
        if tdl:
            claimed = [norm_dmg(x) for x in damages(tdl)]
            real = [norm_dmg(x) for x in damages(effects)]
            for c in claimed:
                if c not in real:
                    add("ERROR", name, "damage-not-dealt",
                        f"tooltip lists {c.replace('|', ' ')} and nothing deals it")
            if len(real) > len(claimed):
                add("WARN", name, "damage-not-listed",
                    f"deals {len(real)} damage instance(s), tooltip lists {len(claimed)}")

        # ---- E. a damaging spell with no damage list at all ----------------------
        if e.get("type") == "SpellData" or name.startswith(("Target_", "Shout_",
                                                            "Projectile_")):
            if damages(effects) and not tdl and e.get("DescriptionParams"):
                add("WARN", name, "no-damage-list",
                    "deals damage and has DescriptionParams but no TooltipDamageList, "
                    "unlike its siblings")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compare the mod's tooltips against what its functors actually do.")
    ap.add_argument("--strict", action="store_true", help="exit 1 on WARN too")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    a = ap.parse_args()

    stats = S.parse_stats()
    found = audit(stats)
    if a.json:
        print(json.dumps(found, indent=2))
        return 1 if any(f["severity"] == "ERROR" for f in found) else 0

    print(f"tooltip audit - {len(stats)} entries\n")
    explained = [f for f in found if (f["entry"], f["check"]) in EXPLAINED_BY]
    found = [f for f in found if (f["entry"], f["check"]) not in EXPLAINED_BY]
    errs = [f for f in found if f["severity"] == "ERROR"]
    warns = [f for f in found if f["severity"] == "WARN"]
    for f in errs + warns:
        print(f"{f['severity']:<6} {f['entry']}  [{f['check']}]")
        print(f"       {f['detail']}")
    if explained:
        print("EXPLAINED - the base tooltip is right and another feature states the rest:")
        for f in explained:
            who = EXPLAINED_BY[(f["entry"], f["check"])]
            print(f"       {f['entry']} [{f['check']}] -> {who} says so")
        print()
    if not found:
        print("clean - every structured tooltip matches what the functors do, or is "
              "explained by a feature that names it.")
    print(f"\n{len(errs)} error(s), {len(warns)} warning(s), {len(explained)} explained")
    print("⚠ Structured fields only. If the DESCRIPTION PROSE states a number, only a "
          "human can\n  check that - this tool cannot read localisation text.")
    return 1 if errs or (a.strict and warns) else 0


if __name__ == "__main__":
    raise SystemExit(main())
