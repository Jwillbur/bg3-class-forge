"""
Triage the BG3 Osiris / Script Extender logs after a live test.

Answers the question every live pass ends on: *did the thing actually happen, and
if not, how far did it get?* Built against the real log format, streaming, because
Osiris logs run to several MB.

    py triage_log.py                     # full Warpblade report from the newest logs
    py triage_log.py --spell PhaseFeint  # one spell's full pipeline
    py triage_log.py --status ANCHORED   # who got a status, and when
    py triage_log.py --errors            # Script Extender errors + module load state
    py triage_log.py --list              # available log sessions
    py triage_log.py --log "<path>"      # a specific Osiris log

WHAT IT CAN PROVE
    A spell's cast pipeline, stage by stage - which events actually fired.
    Status applications, with the entity and the cause.
    Whether the Script Extender loaded the expected modules, and any Lua errors.

WHAT IT CANNOT PROVE  (read this before trusting a negative)
    Interrupts. The Osiris runtime log contains NO reaction/interrupt events at
    all - `ReactionInterruptUsed` is an Ext.Osiris listener, not a logged event.
    So this tool cannot directly confirm Emergency Displacement fired. What it CAN
    show is the second-order evidence: a status the interrupt applies, or a spell
    it casts via UseSpell. An interrupt whose whole effect is AdjustRoll +
    SwapPlaces leaves no trace here.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from collections import defaultdict
from pathlib import Path

# --- mod config -----------------------------------------------------------
# Anchored on the MOD BEING AUDITED (the cwd), not on this file. Until
# 2026-09-06 every path below was the literal string CFG.name, so running
# this from another mod silently audited Warpblade instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402
CFG = modconfig.load(Path.cwd())


LOG_DIR = Path(r"C:\Modding\bg3_extender_logs")
MOD_PREFIXES = (CFG.name, "WARPBLADE")

# The cast pipeline, in the order Osiris emits it. Reaching stage N without N+1 is
# the diagnostic - that is exactly how Phase Feint's dead spell was found (S24.3).
PIPELINE = [
    "StartedPreviewingSpell",
    "StoppedPreviewingSpell",
    "UsingSpell",
    "UsingSpellOnTarget",
    "CastSpell",
    "CastedSpell",
]
TERMINAL_FAIL = "CastSpellFailed"

EVENT_RE = re.compile(r"^>>> event ([A-Za-z_]\w*)\((.*)\)\s*$")
# Rule-action / procedure form of the same events. The `\b` word boundary
# matters: it stops this
# matching DB_GLO_CastedSpell, which is a registry fact, not a cast.
CALL_RE = re.compile(r"\b(CastedSpell|CastSpellFailed|StatusApplied|StatusRemoved)"
                     r"\(\s*(.*?)\s*\)\s*(?:\[|$)")


def split_args(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return [a.strip().strip('"') for a in out]


def short(entity: str) -> str:
    """Trim the UUID tail off an Osiris entity handle for readability."""
    return re.sub(r"_[0-9a-f]{8}-[0-9a-f-]{27,}$", "", entity)


def newest(pattern: str) -> Path | None:
    if not LOG_DIR.is_dir():
        return None
    files = sorted(LOG_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def scan(path: Path, want: re.Pattern | None = None):
    """Stream a log, yielding (event_name, args). Never loads the whole file."""
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if want and not want.search(line):
                continue
            m = EVENT_RE.match(line.rstrip("\n"))
            if m:
                yield m.group(1), split_args(m.group(2))
                continue
            for m2 in CALL_RE.finditer(line):
                yield m2.group(1), split_args(m2.group(2))


# --------------------------------------------------------------------- report --
def collect(path: Path, needle: str | None):
    """Group spell events by spell id, and gather status applications."""
    spells: dict[str, dict] = defaultdict(
        lambda: {"stages": defaultdict(int), "casters": set(), "targets": set(),
                 "failed": 0})
    statuses: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "entities": set(), "removed": 0})

    filt = re.compile(needle, re.I) if needle else re.compile("|".join(MOD_PREFIXES))

    for name, args in scan(path, filt):
        if name in ("StatusApplied", "StatusRemoved"):
            if len(args) >= 2 and filt.search(args[1]):
                rec = statuses[args[1]]
                if name == "StatusApplied":
                    rec["count"] += 1
                    rec["entities"].add(short(args[0]))
                else:
                    rec["removed"] += 1
            continue

        if name not in PIPELINE and name != TERMINAL_FAIL:
            continue
        # spell id is arg[1] normally; UsingSpellOnTarget puts the target at [1]
        spell = args[2] if name == "UsingSpellOnTarget" and len(args) > 2 else (
            args[1] if len(args) > 1 else "")
        if not spell or not filt.search(spell):
            continue
        rec = spells[spell]
        rec["casters"].add(short(args[0]))
        if name == "UsingSpellOnTarget" and len(args) > 1:
            rec["targets"].add(short(args[1]))
        if name == TERMINAL_FAIL:
            rec["failed"] += 1
        else:
            rec["stages"][name] += 1
    return spells, statuses


# NOTE on a discarded signal: the log also carries a 6-argument
# `DB_GLO_CastedSpell(..., n, flag)` database fact, which looks like it ends in a
# cast-success flag. It is NOT one - it is a registry fact, every observed instance
# reads `0, 0`, and it fires for spells that were only ever previewed. Verdicts
# below therefore rest solely on which pipeline EVENTS were emitted, which is
# directly observable and needs no interpretation.


def verdict(rec: dict) -> str:
    """Furthest pipeline stage reached. Nothing here is inferred."""
    st = rec["stages"]
    if st.get("CastedSpell"):
        return "CAST COMPLETED"
    if st.get("CastSpell"):
        return "CAST STARTED, never resolved"
    if st.get("UsingSpell") or st.get("UsingSpellOnTarget"):
        return "COMMITTED, never reached CastSpell"
    if st.get("StartedPreviewingSpell"):
        return "PREVIEWED ONLY - aimed but never committed"
    if rec["failed"]:
        return "NEVER ENTERED PIPELINE - the Phase Feint signature"
    return "no events"


def report(path: Path, ext: Path | None, needle: str | None) -> int:
    print(f"Osiris log : {path.name}  ({path.stat().st_size // 1024:,} KB)")
    if ext:
        print(f"Extender   : {ext.name}")
    print()

    spells, statuses = collect(path, needle)

    if not spells and not statuses:
        print("No matching spell or status events found.")
        print("If you expected some: the spell may never have been clicked, or the")
        print("mod may not have loaded. Run with --errors to check module load state.")
        return 1

    if spells:
        print("SPELLS")
        print("-" * 78)
        for sid in sorted(spells):
            r = spells[sid]
            st = r["stages"]
            print(f"  {sid}")
            print(f"      verdict : {verdict(r)}")
            trace = []
            for stage in PIPELINE:
                trace.append(f"{stage}={st[stage]}" if st.get(stage) else f"{stage}=-")
            print(f"      pipeline: {'  '.join(trace)}")
            if r["failed"]:
                print(f"      CastSpellFailed x{r['failed']}"
                      f"   (note: vanilla spells emit this too - not proof of a bug)")
            if r["targets"]:
                print(f"      targets : {', '.join(sorted(r['targets'])[:4])}")
            print()

    if statuses:
        print("STATUSES")
        print("-" * 78)
        for sid in sorted(statuses):
            r = statuses[sid]
            ents = ", ".join(sorted(r["entities"])[:4]) or "-"
            print(f"  {sid:<34} applied x{r['count']:<4} removed x{r['removed']:<4} -> {ents}")
        print()

    print("REMINDER: interrupts leave no trace in this log. An interrupt whose effect")
    print("is only AdjustRoll + SwapPlaces cannot be confirmed here - look for a status")
    print("it applies, or watch the combat log in game.")
    return 0


def errors(ext: Path | None) -> int:
    if not ext or not ext.exists():
        print("No Extender Runtime log found.")
        return 1
    txt = io.open(ext, encoding="utf-8", errors="replace").read()
    txt = re.sub(r"\x1b\[[0-9;]*m|\[38;2;[0-9;]*m", "", txt)   # strip ANSI colour

    print(f"Extender log: {ext.name}\n")
    # The block is logged once per context (client and server), so dedupe.
    mods = dict(re.findall(r"^\s+'([^']+)': SE v(\d+)", txt, re.M))
    print("Loaded modules with a Script Extender component:")
    for n, v in sorted(mods.items()):
        print(f"   {n:<34} SE v{v}")
    print("   (Warpblade ships no Lua, so its absence here is correct.)\n")

    bad = [l for l in txt.split("\n")
           if re.search(r"\b(ERROR|FATAL|failed|exception|stack traceback)\b", l, re.I)
           and "LogFailedCompile" not in l]
    print(f"Error-ish lines: {len(bad)}")
    for l in bad[:25]:
        print("   " + l.strip()[:150])
    if not bad:
        print("   none - clean load.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", help="specific Osiris log path")
    ap.add_argument("--spell", help="filter to spells matching this text")
    ap.add_argument("--status", help="filter to statuses matching this text")
    ap.add_argument("--errors", action="store_true", help="Extender errors + load state")
    ap.add_argument("--list", action="store_true", help="list available log sessions")
    a = ap.parse_args()

    if a.list:
        if not LOG_DIR.is_dir():
            print(f"log dir not found: {LOG_DIR}")
            return 1
        for p in sorted(LOG_DIR.glob("Osiris Runtime*.log"),
                        key=lambda p: p.stat().st_mtime, reverse=True):
            print(f"  {p.stat().st_size // 1024:>7,} KB  {p.name}")
        return 0

    ext = newest("Extender Runtime*.log")
    if a.errors:
        return errors(ext)

    path = Path(a.log) if a.log else newest("Osiris Runtime*.log")
    if not path or not path.exists():
        print(f"No Osiris log found in {LOG_DIR}", file=sys.stderr)
        return 1
    return report(path, ext, a.spell or a.status)


if __name__ == "__main__":
    raise SystemExit(main())
