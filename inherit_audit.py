"""
Audit what the mod inherits through `using` and never acknowledges.

WHY THIS EXISTS. On 2026-08-19, live play turned up Warped Blade's tooltip
reading "Undead and Constructs can't Bleed." Nothing in the cantrip applies
BLEEDING. The cause was one line:

    using "Target_Slash_New"

Target_Slash_New is vanilla's Slash - a BLEED maneuver - and Warped Blade never
overrode its `ExtraDescription`, so it inherited the caveat verbatim. The whole
toolchain was silent, because every individual thing about that entry was legal.

`using` inherits PLAYER-FACING TEXT exactly as silently as it inherits mechanics,
and a wrong inherited string is invisible to a validator that only asks whether
fields resolve. So this tool does not ask "is it valid" - it asks "did you MEAN
this", and for text fields it prints the actual sentence the player reads, which
is the only form in which the Bleed line is obvious at a glance.

Findings are advisory. Real inheritance is the point of `using`; most of what
this reports is correct and intentional. Acknowledge those once into the
baseline and the report stays down to genuinely new inheritance.

    py inherit_audit.py                    # audit, exit 1 if unreviewed TEXT findings
    py inherit_audit.py --all              # include MECHANIC and INFO tiers
    py inherit_audit.py --entry NAME       # full field-by-field provenance for one entry
    py inherit_audit.py --update-baseline  # accept current findings as reviewed
    py inherit_audit.py --no-baseline      # ignore the baseline, report everything
"""

from __future__ import annotations

import argparse
import re
import sys
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


MOD = CFG.root
STATS = CFG.stats
LOCA = CFG.loca
VANILLA_LOCA = Path(r"C:\Modding\bg3_unpacked\english.xml")
BASELINE = Path(__file__).resolve().parent / "inherit-baseline.txt"

# ---------------------------------------------------------------------------
# What is worth flagging, and why it is in the tier it is in.
# ---------------------------------------------------------------------------

# TEXT: the player reads this. An inherited value here does not fail, it LIES -
# which is strictly worse, because nothing surfaces it but a screenshot.
TEXT_FIELDS = {
    "DisplayName", "Description", "ExtraDescription", "DescriptionParams",
    "ShortDescription", "ShortDescriptionParams", "TooltipDamageList",
    "TooltipAttackSave", "TooltipStatusApply", "TooltipOnMiss", "TooltipOnSave",
    "TooltipUpcastDescription", "TooltipPermanentWarnings", "LoreDescription",
}

# MECHANIC: inherited behaviour. Usually the whole reason for the `using`, but
# an inherited functor list is how a spell quietly keeps doing something the
# child never asked for - the same shape of bug as the text case.
MECHANIC_FIELDS = {
    "SpellSuccess", "SpellFail", "SpellProperties", "SpellRoll",
    "OnApplyFunctors", "OnRemoveFunctors", "OnTickFunctors", "StatsFunctors",
    "Boosts", "Passives", "Conditions", "RequirementConditions",
    "TargetConditions", "UseCosts", "HitCosts", "Cooldown", "StatusPropertyFlags",
    "SpellFlags", "StatusGroups", "TickType", "RemoveEvents", "StackId",
}

# Fields whose inheritance is never interesting - shared plumbing, animation
# banks, sounds. Reporting these would bury the two tiers that matter.
BORING_FIELDS = {
    "SpellType", "StatusType", "AreaRadius", "TargetRadius", "TargetFloor",
    "TargetCeiling", "PreviewCursor", "CastTextEvent", "VerbalIntent",
    "HitAnimationType", "SpellAnimationIntentType", "SpellStyleGroup",
    "AlternativeCastTextEvents", "CycleConditions", "DualWieldingUseCosts",
    "WeaponTypes", "Level", "SpellSchool",
}

HANDLE_RE = re.compile(r"^h[0-9a-f]{8}g[0-9a-f]{4}g[0-9a-f]{4}g[0-9a-f]{4}g[0-9a-f]{12}"
                       r"|^h[0-9a-f]{32,}")
CONTENT_RE = re.compile(r'contentuid="([^"]+)"[^>]*>(.*?)</content>', re.S)


def load_loca() -> dict[str, str]:
    """handle -> the English sentence the player actually sees."""
    out: dict[str, str] = {}
    for path in (VANILLA_LOCA, LOCA):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for h, body in CONTENT_RE.findall(text):
            out[h.split(";")[0]] = re.sub(r"\s+", " ", body).strip()
    return out


def resolve(value: str, loca: dict[str, str]) -> str:
    """Render a field value, expanding a loca handle into its sentence."""
    key = value.split(";")[0].strip()
    if HANDLE_RE.match(key) and key in loca:
        text = loca[key]
        return f'"{text}"' if len(text) <= 160 else f'"{text[:157]}..."'
    return value


def field_source(entry: dict, field: str, by_name: dict[str, dict]) -> str | None:
    """Which entry in the `using` chain actually supplies this field."""
    if field in entry["data"]:
        return entry["name"]
    for parent_name in entry.get("using_chain", []):
        parent = by_name.get(parent_name)
        if parent and field in parent["data"]:
            return parent_name
    return None


def load_baseline() -> set[str]:
    if not BASELINE.is_file():
        return set()
    return {ln.strip() for ln in BASELINE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="include MECHANIC and INFO tiers, not just TEXT")
    ap.add_argument("--entry", help="full field-by-field provenance for one entry")
    ap.add_argument("--update-baseline", action="store_true",
                    help="record current findings as reviewed")
    ap.add_argument("--no-baseline", action="store_true",
                    help="ignore the baseline and report everything")
    args = ap.parse_args()

    vanilla = ci.build_index()
    mod_entries: list[dict] = []
    for path in sorted(STATS.glob("*.txt")):
        for e in ci.parse_file(path):
            e["root"] = CFG.name
            mod_entries.append(e)
    if not mod_entries:
        print(f"FATAL: no stats entries parsed from {STATS}", file=sys.stderr)
        return 2

    # One namespace: the mod's own entries shadow vanilla, which is what the
    # engine does. Resolve `using` over the merged set so a chain can cross from
    # a mod entry into a vanilla parent, which is the normal case.
    by_name = {e["name"]: e for e in vanilla}
    by_name.update({e["name"]: e for e in mod_entries})
    ci.resolve_using(list(by_name.values()))

    loca = load_loca()
    print(f"auditing {len(mod_entries)} mod entries "
          f"({len(loca):,} loca strings resolved)\n", file=sys.stderr)

    # ---- single-entry provenance mode ---------------------------------------
    if args.entry:
        e = by_name.get(args.entry)
        if not e:
            print(f"no entry named {args.entry}", file=sys.stderr)
            return 2
        chain = e.get("using_chain", [])
        print(f"{e['name']}   type={e.get('type')}   file={e.get('file')}")
        print(f"  using chain: {' -> '.join(chain) if chain else '(none)'}\n")
        eff = ci.effective_fields(e, by_name)
        own = sorted(k for k in eff if k in e["data"])
        inherited = sorted(k for k in eff if k not in e["data"])
        print(f"  --- own ({len(own)}) ---")
        for k in own:
            print(f"    {k:<28} {resolve(eff[k], loca)[:150]}")
        print(f"\n  --- inherited ({len(inherited)}) ---")
        for k in inherited:
            src = field_source(e, k, by_name)
            tier = ("TEXT" if k in TEXT_FIELDS else
                    "MECH" if k in MECHANIC_FIELDS else "    ")
            print(f"    [{tier}] {k:<24} <- {src}")
            print(f"           {resolve(eff[k], loca)[:150]}")
        return 0

    # ---- the audit ----------------------------------------------------------
    baseline = set() if args.no_baseline else load_baseline()
    findings: list[tuple[str, str, str, str, str]] = []   # tier, entry, field, src, value

    for e in mod_entries:
        chain = e.get("using_chain", [])
        if not chain:
            continue
        eff = ci.effective_fields(e, by_name)
        for field, value in sorted(eff.items()):
            if field in e["data"] or field in BORING_FIELDS:
                continue
            if not str(value).strip():
                continue          # inheriting a blank is not a claim about anything
            tier = ("TEXT" if field in TEXT_FIELDS else
                    "MECHANIC" if field in MECHANIC_FIELDS else "INFO")
            src = field_source(e, field, by_name) or "?"
            findings.append((tier, e["name"], field, src, resolve(value, loca)))

    order = {"TEXT": 0, "MECHANIC": 1, "INFO": 2}
    findings.sort(key=lambda f: (order[f[0]], f[1], f[2]))

    shown = [f for f in findings if args.all or f[0] == "TEXT"]
    keys = [f"{t}|{n}|{fld}|{src}" for t, n, fld, src, _ in shown]

    if args.update_baseline:
        BASELINE.write_text(
            "# Inherited fields reviewed and accepted as intentional.\n"
            "# Regenerate with: py inherit_audit.py --all --update-baseline\n"
            "# A line disappearing from here means the inheritance CHANGED - re-review it.\n"
            + "\n".join(sorted(f"{t}|{n}|{fld}|{src}"
                              for t, n, fld, src, _ in findings)) + "\n",
            encoding="utf-8")
        print(f"baseline updated: {len(findings)} finding(s) accepted", file=sys.stderr)
        return 0

    new = [(f, k) for f, k in zip(shown, keys) if k not in baseline]

    for (tier, name, field, src, value), _ in new:
        print(f"{tier:<9} {name}")
        print(f"          inherits {field} from {src}")
        print(f"          {value}")
        print()

    # Summary on stdout, deliberately: it has to land AFTER the findings, and a
    # piped stderr interleaves ahead of them.
    n_text = sum(1 for (t, *_), _ in new if t == "TEXT")
    suppressed = len(shown) - len(new)
    tail = f", {suppressed} already reviewed" if suppressed else ""
    print(f"{len(new)} unreviewed finding(s){tail} "
          f"(of {len(findings)} inherited fields in total)")

    if not args.all and any(f[0] != "TEXT" for f in findings):
        print("run with --all to see MECHANIC and INFO tiers too")
    if n_text:
        print("\nTEXT findings are what the PLAYER READS. Either override the field "
              "(blanking it is vanilla's own idiom) or accept it with "
              "--update-baseline.")
    return 1 if n_text else 0


if __name__ == "__main__":
    raise SystemExit(main())
