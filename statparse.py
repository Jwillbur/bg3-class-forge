# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse BG3 stats files and the functor strings inside them.

⭐ WHY THIS FILE EXISTS, 2026-09-06
    These four functions lived in `bg3/Warpblade/tools/sim.py`. `tooltip_audit.py`
    imported them - and then `tooltip_audit.py` moved into `forge/` while `sim.py`
    deliberately did NOT, because sim models one mod's mechanics and this framework
    is public.

    The result was a shared tool that only ran inside one mod:

        ModuleNotFoundError: No module named 'sim'

    Every mod but Warpblade got a traceback instead of a tooltip audit. Copying the
    splitter into the forge would have worked and would have left two copies of a
    parser whose whole job is being subtle, so it is moved here instead and `sim.py`
    imports it back.

⚠ The splitters are paren-aware ON PURPOSE. A naive `.split(';')` breaks on a `;`
    inside `IF(...)`, silently halving a functor list - which makes every consumer
    optimistic in exactly the way these tools exist to prevent.
"""
from __future__ import annotations

import re
from pathlib import Path

ENTRY_RE = re.compile(r'^new entry "([^"]+)"')
DATA_RE = re.compile(r'^data "([^"]+)"\s+"(.*)"\s*$')
IF_RE = re.compile(r"^IF\((.*)\):(.*)$", re.S)


def parse_stats(d: Path) -> dict:
    """{entry name: {field: value}} across one Stats/Generated/Data directory."""
    out: dict[str, dict] = {}
    if not d.is_dir():
        raise SystemExit("no stats dir at %s" % d)
    for f in sorted(d.glob("*.txt")):
        cur = None
        for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            m = ENTRY_RE.match(line)
            if m:
                cur = {"_name": m.group(1), "_file": f.name}
                out[m.group(1)] = cur
                continue
            m = DATA_RE.match(line)
            if m and cur is not None:
                cur[m.group(1)] = m.group(2)
    return out


def split_functors(blob: str) -> list[str]:
    """Split a StatsFunctors/SpellSuccess string on top-level `;`.

    ⚠ Naive `.split(';')` is wrong - a `;` inside IF(...) parentheses separates
      nothing. That mistake silently halves the functor list and makes every
      consumer optimistic, which is the failure mode these tools exist to avoid.
    """
    out, depth, cur = [], 0, []
    for ch in blob:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == ";" and depth == 0:
            if "".join(cur).strip():
                out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def split_condition(cond: str) -> list[str]:
    """Top-level ` and ` terms of an IF condition, parens respected."""
    parts, depth, cur = [], 0, []
    i = 0
    while i < len(cond):
        ch = cond[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and cond[i:i + 5] == " and ":
            parts.append("".join(cur).strip())
            cur = []
            i += 5
            continue
        cur.append(ch)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def parse_functor(f: str) -> tuple[str | None, str]:
    """('condition' or None, 'the call')."""
    m = IF_RE.match(f.strip())
    return (m.group(1), m.group(2).strip()) if m else (None, f.strip())
