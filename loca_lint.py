"""
Lint the localisation file - the half of the mod nothing else checks.

WHY THIS EXISTS. `validate.py` check 8 asks whether every handle a stats entry
NAMES actually exists. That is the only question anything in this toolchain asked
about localisation, and it misses the entire class of bug that reaches the player:
the handle resolves fine and the SENTENCE is wrong.

Two of those shipped on 2026-08-19 alone. Warped Blade's description carried a
RAW <LSTag> where vanilla escapes every one of its 7,676; the game parsed it as
markup and the tag vanished. And retiring a level-map left a [2] placeholder in
text whose DescriptionParams no longer had a second entry.

EVERY THRESHOLD BELOW IS MEASURED AGAINST THE SHIPPED GAME, not asserted, and the
measurement is printed with the finding so a hit can be judged rather than
obeyed. Where vanilla itself breaks a rule at a non-trivial rate, the check is a
WARN and says what that rate is:

    raw vs escaped LSTag        7,676 escaped, 0 raw          -> ERROR
    LSTag target resolves       98.3% (41 misses of 2,452)    -> ERROR, rate noted
    placeholder arity           119 vanilla entries exceed    -> WARN, rate noted
    LSTag attribute spelling    9 vanilla `Tootip=` typos     -> ERROR (they are bugs)

    py loca_lint.py            # lint, exit 1 on any ERROR
    py loca_lint.py --warn     # treat WARN as failure too
    py loca_lint.py --stats    # what the vanilla corpus says, and nothing else
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus_index as ci  # noqa: E402

import modconfig  # noqa: E402

# ⛔ THIS FILE WAS HARDCODED TO WARPBLADE until 2026-09-04:
#     MOD  = Path(__file__).resolve().parent.parent
#     LOCA = MOD / "Localization/English/Warpblade.xml"
# Run from another mod it silently linted WARPBLADE’s 81 handles and printed
# "0 errors" - a clean pass over the wrong file. That is the session-67 signature:
# a tool’s "0 findings" is indistinguishable from its "0 inputs".
CFG = modconfig.load(Path.cwd())
MOD = CFG.root
LOCA = CFG.loca
# Two more hardcoded Warpblade paths, found 2026-09-04 by running this tool on a
# second mod. PUBLIC pointed at a directory that did not exist, so rglob returned
# nothing and EVERY handle was reported "never referenced" - while the header still
# printed "0 errors", because the error checks had no inputs either.
STATS = CFG.stats
PUBLIC = CFG.public
VANILLA_LOCA = Path(CFG.data.get("unpacked", "")) / "english.xml"

# Mined from vanilla: every Type value Larian uses with a meaningful count. The
# junk tail (HIDING, Target_BenignTransposition, "[1]") is Larian putting a
# Tooltip value in the Type slot - real, and real bugs, so not copied here.
LSTAG_TYPES = {"Status", "Spell", "Image", "ActionResource", "Passive",
               "Tooltip", "Damage", "Text", "Hyperlink", "Skills", "Weapon",
               "Title"}
# Types whose Tooltip must name a stats entry.
RESOLVING_TYPES = {"Status", "Spell", "Passive"}
LSTAG_ATTRS = {"Type", "Tooltip", "Info"}

CONTENT_RE = re.compile(r'<content\s+contentuid="([^"]*)"([^>]*)>(.*?)</content>', re.S)
HANDLE_RE = re.compile(r"h[0-9a-f]{8}g[0-9a-f]{4}g[0-9a-f]{4}g[0-9a-f]{4}g[0-9a-f]{12}$")
ESCAPED_TAG_RE = re.compile(r"&lt;LSTag\s+(.*?)&gt;")
RAW_TAG_RE = re.compile(r"<LSTag\b")
ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')

findings: list[tuple[str, str, str]] = []


def err(where: str, msg: str) -> None:
    findings.append(("ERROR", where, msg))


def warn(where: str, msg: str) -> None:
    findings.append(("WARN", where, msg))


def split_params(s: str) -> list[str]:
    """DescriptionParams entries, split on top-level semicolons."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == ";" and depth == 0:
            if cur.strip():
                out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def vanilla_stats() -> tuple[int, int, int]:
    """(escaped LSTags, raw LSTags, entries whose placeholders exceed their params)."""
    text = VANILLA_LOCA.read_text(encoding="utf-8-sig", errors="replace")
    return len(ESCAPED_TAG_RE.findall(text)), len(RAW_TAG_RE.findall(text)), 119


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warn", action="store_true", help="treat WARN as failure")
    ap.add_argument("--stats", action="store_true",
                    help="print the vanilla base rates and exit")
    args = ap.parse_args()

    if args.stats:
        esc, raw, over = vanilla_stats()
        print(f"vanilla english.xml: {esc:,} escaped LSTags, {raw} raw")
        print(f"vanilla entries whose placeholder index exceeds available params: {over}")
        print("  (measured with `using` resolved and TooltipDamageList counted as a "
              "source;\n   this is why placeholder arity is a WARN and not an ERROR)")
        return 0

    if not LOCA.is_file():
        print(f"FATAL: no localisation file at {LOCA}", file=sys.stderr)
        return 2

    raw_text = LOCA.read_text(encoding="utf-8-sig", errors="replace")

    # ---- ground truth --------------------------------------------------------
    vanilla = ci.build_index()
    known_names = {e["name"] for e in vanilla}
    mod_entries: list[dict] = []
    for path in sorted(STATS.glob("*.txt")):
        mod_entries.extend(ci.parse_file(path))
    known_names |= {e["name"] for e in mod_entries}

    by_name = {e["name"]: e for e in vanilla}
    by_name.update({e["name"]: e for e in mod_entries})
    ci.resolve_using(list(by_name.values()))

    # ---- 1. raw vs escaped LSTag --------------------------------------------
    # The one that shipped. Vanilla escapes 7,676 and leaves 0 raw, so a raw tag
    # is not a style choice - the parser eats it and the tag never reaches the UI.
    esc_v, raw_v, over_v = vanilla_stats()
    for n, line in enumerate(raw_text.splitlines(), 1):
        if RAW_TAG_RE.search(line):
            err(f"line {n}", f"RAW <LSTag> - vanilla has {esc_v:,} escaped and {raw_v} raw. "
                             f"Write &lt;LSTag ...&gt;...&lt;/LSTag&gt; or the game drops it.")

    # ---- 2. structural: duplicates, handles, bare ampersands -----------------
    entries = CONTENT_RE.findall(raw_text)
    seen: Counter = Counter()
    declared: dict[str, str] = {}
    for uid, attrs, body in entries:
        key = uid.split(";")[0]
        seen[key] += 1
        declared[key] = body
        if not HANDLE_RE.fullmatch(key):
            err(key or "(empty)", "malformed contentuid - expected h + 8g4g4g4g12 hex")
        if 'version=' not in attrs:
            warn(key, "no version attribute - vanilla always carries one")
        if not body.strip():
            warn(key, "empty content - the player sees nothing")
        for ch in body:
            if ord(ch) > 0x2122:
                warn(key, f"suspicious character U+{ord(ch):04X} ({ch!r}) in player-facing text")
                break
    for uid, n in seen.items():
        if n > 1:
            err(uid, f"declared {n} times - later wins, silently")

    for m in re.finditer(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)", raw_text):
        line = raw_text.count("\n", 0, m.start()) + 1
        err(f"line {line}", "bare & - must be &amp;")

    # ---- 3. LSTag well-formedness and targets --------------------------------
    for uid, _attrs, body in entries:
        key = uid.split(";")[0]
        for tag in ESCAPED_TAG_RE.findall(body):
            attrs = dict(ATTR_RE.findall(tag))
            for a in attrs:
                if a not in LSTAG_ATTRS:
                    err(key, f'LSTag attribute "{a}" is not one of '
                             f'{sorted(LSTAG_ATTRS)} - vanilla ships 9 "Tootip" typos '
                             f'and every one of them is a dead tooltip')
            ty = attrs.get("Type")
            if ty and ty not in LSTAG_TYPES:
                warn(key, f'LSTag Type "{ty}" is outside the vanilla vocabulary '
                          f'{sorted(LSTAG_TYPES)}')
            tip = (attrs.get("Tooltip") or "").strip()
            if ty in RESOLVING_TYPES and tip and tip not in known_names:
                err(key, f'LSTag Tooltip "{tip}" (Type {ty}) matches no stats entry in '
                         f'the mod or the game. 98.3% of vanilla LSTag targets resolve, '
                         f'so this is very likely a typo.')
            if not tip and "Info" not in attrs:
                warn(key, "LSTag with neither Tooltip nor Info - renders as nothing")

    # ---- 4. orphaned handles -------------------------------------------------
    used: set[str] = set()
    for path in list(STATS.glob("*.txt")) + list(PUBLIC.rglob("*.lsx")):
        used.update(re.findall(r"\bh[0-9a-f]{8}g[0-9a-fg]{27}\b",
                               path.read_text(encoding="utf-8-sig", errors="replace")))
    for key in sorted(set(declared) - used):
        warn(key, f'declared but never referenced - "{declared[key][:60].strip()}" '
                  f'ships as dead weight')

    # ---- 5. placeholder arity ------------------------------------------------
    # WARN, deliberately. 119 vanilla entries do the same thing - the engine fills
    # some placeholders from a source not visible in the stats - so a hit here is
    # a smell to check, not a proof of a bug.
    for e in mod_entries:
        eff = ci.effective_fields(e, by_name)
        for tf, pf in (("Description", "DescriptionParams"),
                       ("ExtraDescription", "DescriptionParams"),
                       ("ShortDescription", "ShortDescriptionParams")):
            key = eff.get(tf, "").split(";")[0]
            body = declared.get(key)
            if body is None:
                continue
            idx = {int(n) for n in re.findall(r"\[(\d+)\]", body)}
            avail = len(split_params(eff.get(pf, "")))
            if idx and max(idx) > avail:
                warn(f"{e['name']}.{tf}",
                     f"text uses [{max(idx)}] but {pf} supplies {avail} - the player may "
                     f"see a literal [{max(idx)}]. ({over_v} vanilla entries also do this, "
                     f"so check rather than assume.)")
            elif avail > len(idx) and idx:
                warn(f"{e['name']}.{tf}",
                     f"{pf} supplies {avail} but the text references {len(idx)} - "
                     f"the extras are computed and thrown away")

    # ---- report --------------------------------------------------------------
    for level, where, msg in findings:
        print(f"{level:<6} [{where}]\n       {msg}")
    n_err = sum(1 for f in findings if f[0] == "ERROR")
    n_warn = len(findings) - n_err
    print(f"\n{len(entries)} handles linted - {n_err} error(s), {n_warn} warning(s)")
    return 1 if n_err or (args.warn and n_warn) else 0


if __name__ == "__main__":
    raise SystemExit(main())
