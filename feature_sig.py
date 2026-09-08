#!/usr/bin/env python3
"""Detect when a feature's implementation changed after it was last verified.

    py tools/feature_sig.py                 # current signature vs recorded, per feature
    py tools/feature_sig.py --bless-history # recover each recorded sig from git history
    py tools/feature_sig.py --bless <id>    # record the CURRENT state as verified

THE PROBLEM THIS SOLVES
-----------------------
`features.json` is hand-maintained on purpose: no static check can know whether a
feature behaves correctly in play, and `dashboard.py` says so in as many words. But
"verified" is a claim about a MOMENT, and nothing was watching the implementation after
that moment. Warp Assault was marked verified against Pass 4 on 2026-08-19; its cast
effect was then rewritten three times over the following three days, and the board went
on showing a green VERIFIED badge the whole way. That is precisely the "green light
nobody earned" the panel exists to prevent, arriving through the back door.

Status still cannot be derived - but STALENESS can. A signature over the files that
implement a feature says nothing about whether it works, and everything about whether
the thing that was proven is the thing that is shipping.

WHAT GOES INTO A SIGNATURE
--------------------------
The feature's own stats blocks, plus any MultiEffectInfo WE ship that those blocks
reference. Comment lines and blank lines are stripped first, deliberately: this file's
neighbours carry long explanatory comments that get rewritten constantly, and a reworded
comment is not a reason to re-run a play session. Only lines the game reads count.

WHY THE RECORDED SIGNATURES COME OUT OF GIT
-------------------------------------------
Blessing everything at today's state would stamp "verified" on three features that
demonstrably changed after they were proven - it would launder the exact lie this is
meant to expose. `--bless-history` instead reads each feature's verification date out of
its own evidence text, checks out that tree from git, and signs THAT. Anything modified
since then shows up as changed on the next board load, without anyone having to remember.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

# --- mod config -----------------------------------------------------------
# Anchored on the MOD BEING AUDITED (the cwd), not on this file. Until
# 2026-09-06 every path below was the literal string CFG.name, so running
# this from another mod silently audited Warpblade instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402
CFG = modconfig.load(Path.cwd())


# ⚠ A cp1252 console (Git Bash) cannot encode this file's own warning characters,
# and the failure is a hard UnicodeEncodeError mid-print. Caught by
# tools/encoding_gate.py BEFORE this shipped - the fourth time in one day that
# adding a warning sign to a warning message reopened the bug, and the first time
# a machine caught it instead of a user.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


MOD = CFG.root
ROOT = CFG.root.parent.parent
FEATURES = MOD / "features.json"
STATS_REL = "bg3/Warpblade/Public/Warpblade/Stats/Generated/Data"
MEI_REL = "bg3/Warpblade/Public/Warpblade/MultiEffectInfos"

EFFECT_FIELDS = ("PrepareEffect", "CastEffect", "TargetEffect", "HitEffect",
                 "PositionEffect", "BeamEffect", "StatusEffect")
DATE = re.compile(r"(20\d\d-\d\d-\d\d)")


def _git(*args, cwd=ROOT):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else None


def _strip(text: str) -> str:
    """Only the lines the game reads: no comments, no blanks, no trailing space."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        out.append(s)
    return "\n".join(out)


def _blocks(stats_text: str, stem: str) -> list:
    """Every `new entry` block whose name contains the stem."""
    found = []
    for blk in re.split(r"(?=^new entry )", stats_text, flags=re.M)[1:]:
        m = re.match(r'new entry "([^"]+)"', blk)
        if m and stem.lower() in m.group(1).lower():
            found.append(blk)
    return found


def collect(feature: dict, read) -> tuple:
    """(ordered [(source, text)], mei_guids) - everything this feature is made of.

    ⚠ SHARED BY signature() AND fields() ON PURPOSE. If the diff walked a different
    entry set than the signature, the diff could report "nothing changed" about a
    feature the signature calls CHANGED - a tool contradicting itself, which is worse
    than the tool not existing. One collector, two consumers, and an acceptance
    control asserts they agree.
    """
    stem = feature["name"].split(" - ")[0].replace(" ", "").replace("'", "")
    if not stem:
        return [], set()

    stats_files = ["Interrupt.txt", "Passive.txt", "Spell_Shout.txt",
                   "Spell_Target.txt", "Status_BOOST.txt"]
    # every entry we ship, by name, so `using` can be followed
    all_blocks = {}
    for fname in stats_files:
        text = read(f"{STATS_REL}/{fname}")
        if text is None:
            continue
        for blk in re.split(r"(?=^new entry )", text, flags=re.M)[1:]:
            m = re.match(r'new entry "([^"]+)"', blk)
            if m:
                all_blocks[m.group(1)] = (fname, blk)

    # Start from the feature's own entries, then pull in everything they inherit from.
    # INHERITANCE IS PART OF THE IMPLEMENTATION: Perfect Convergence declares no
    # CastEffect of its own, it `using`s Warp Assault's - so when Warp Assault's cast
    # effect was rewritten, Perfect Convergence's visuals changed too. The first version
    # of this signed only the feature's own block and reported Perfect Convergence
    # unchanged through three rewrites of the effect it actually plays.
    wanted, queue = [], [n for n in all_blocks if stem.lower() in n.replace(" ", "").lower()]
    seen = set()
    while queue:
        n = queue.pop(0)
        if n in seen or n not in all_blocks:
            continue
        seen.add(n)
        wanted.append(n)
        u = re.search(r'^using "([^"]+)"', all_blocks[n][1], re.M)
        if u:
            queue.append(u.group(1))

    pieces = []
    mei_guids = set()
    for n in wanted:
        fname, blk = all_blocks[n]
        pieces.append((f"{fname}:{n}", blk))
        for field in EFFECT_FIELDS:
            m = re.search(r'^data "%s" "([0-9a-f-]{36})"' % field, blk, re.M)
            if m:
                mei_guids.add(m.group(1))

    # Only MEIs we ship. A vanilla GUID is a pointer at Larian's file, which cannot
    # change under us; ours can, and that is what rewrote Warp Assault three times.
    for guid in sorted(mei_guids):
        text = read(f"{MEI_REL}/{guid}.lsx")
        if text is not None:
            pieces.append((f"MEI {guid[:8]}", text))

    return pieces, mei_guids


def signature(feature: dict, read) -> tuple:
    """(hex digest, [what went into it]) for one feature, or (None, []) if nothing did."""
    pieces, _ = collect(feature, read)
    if not pieces:
        return None, []
    parts = [_strip(text) for _src, text in pieces]
    h = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]
    return h, [src for src, _ in pieces]


def _focus(old: str, new: str, width: int = 130) -> tuple:
    """Trim two strings around their FIRST DIFFERENCE, not from the right.

    ⚠ Was `[:160]` applied to each printed line, so a change near the END of a long
    condition rendered as two IDENTICAL lines - the tool reported drift and then made
    it unreadable. Queue item 81. Hit for real on 2026-09-01 while hunting mod bugs:
    Emergency Displacement showed a was/now pair with no visible difference.
    """
    i = 0
    while i < min(len(old), len(new)) and old[i] == new[i]:
        i += 1
    start = max(0, i - 25)
    lead = "..." if start else ""
    return (lead + old[start:start + width] + ("..." if len(old) > start + width else ""),
            lead + new[start:start + width] + ("..." if len(new) > start + width else ""))
    return h, [src for src, _ in pieces]


FIELD = re.compile(r'^\s*(?:data|type|using)\s+"([^"]+)"(?:\s+"([^"]*)")?', re.M)


def fields(feature: dict, read) -> dict:
    """{source: {field: value}} - the same material signature() hashes, parsed.

    The hash answers "did this change". This answers "what changed", which is the
    question you actually have when a drift line appears: on 2026-08-29 the report
    said two features had drifted and it took a git archaeology detour to learn that
    one was a known fix and the other was not.

    LSX (MultiEffectInfo) is not key/value in the same way, so it is reduced to its
    attribute id/value pairs - enough to name WHICH effect attribute moved.
    """
    out = {}
    pieces, _ = collect(feature, read)
    for src, text in pieces:
        if src.startswith("MEI "):
            out[src] = {m.group(1): m.group(2) for m in
                        re.finditer(r'id="([^"]+)"\s+(?:type="[^"]*"\s+)?value="([^"]*)"',
                                    text)}
        else:
            out[src] = {m.group(1): (m.group(2) or "") for m in FIELD.finditer(text)}
    return out


def diff_fields(before: dict, after: dict) -> list:
    """[(source, field, old, new)] with None meaning absent on that side."""
    rows = []
    for src in sorted(set(before) | set(after)):
        b, a = before.get(src, {}), after.get(src, {})
        if src not in before:
            rows.append((src, "*", None, "ENTRY ADDED"))
            continue
        if src not in after:
            rows.append((src, "*", "ENTRY REMOVED", None))
            continue
        for k in sorted(set(b) | set(a)):
            if b.get(k) != a.get(k):
                rows.append((src, k, b.get(k), a.get(k)))
    return rows


def working_read(rel):
    p = ROOT / rel
    try:
        return p.read_text(encoding="utf-8-sig")
    except OSError:
        return None


def commit_read(sha):
    def read(rel):
        return _git("show", f"{sha}:{rel}")
    return read


def verified_date(feature: dict):
    """The date the feature was proven, from its own evidence text."""
    for key in ("evidence", "pass", "note"):
        m = DATE.search(str(feature.get(key, "")))
        if m:
            return m.group(1)
    return None


def load():
    # A mod with no features.json has had nothing blessed yet - usually because it has
    # never been verified in a running game. That is a COVERAGE GAP to state, not a
    # traceback: a tool that crashes on a mod that is not Warpblade reads as broken and
    # gets dropped from the sweep. Same shape as tooltip_audit's 2026-09-07 crash.
    if not FEATURES.is_file():
        print(f"no {FEATURES.name} in {MOD.name} - nothing has been blessed yet, so there "
              f"is no baseline to drift from.")
        print("  NOT CHECKED. Bless a feature after a live pass confirms it works.")
        raise SystemExit(2)
    return json.loads(FEATURES.read_text(encoding="utf-8"))


def save(data):
    FEATURES.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")


def report(data) -> int:
    changed = unknown = 0
    for f in data["features"]:
        cur, _ = signature(f, working_read)
        rec = f.get("verified_sig")
        if cur is None:
            state, mark = "no signable files", "-"
        elif rec is None:
            state, mark = "never blessed", "?"
            unknown += 1
        elif rec == cur:
            state, mark = f"unchanged since {f.get('verified_at','?')}", "="
        else:
            state, mark = f"CHANGED since {f.get('verified_at','?')}", "!"
            changed += 1
        print(f" {mark} {f['name']:<38} {f.get('status','?'):<10} {state}")
    print(f"\n{changed} changed since verified, {unknown} never blessed")
    return 1 if changed else 0


def _commit_on(date: str):
    """The last commit on or before `date` - the tree as it stood when blessed."""
    sha = _git("log", "-1", "--until", f"{date} 23:59:59", "--format=%H")
    return sha.strip() or None


def show_diff(data, only=None) -> int:
    """Say WHAT changed, not just THAT it changed.

    ⚠ The hash answers a yes/no question. On 2026-08-29 `feature_sig` reported two
      features drifted and answering "is this drift expected?" took a git archaeology
      detour - one turned out to be a known shipped fix, the other was unexplained.
      That detour is the entire reason this exists.

    ⚠ IT DIFFS AGAINST THE COMMIT THE FEATURE WAS BLESSED ON, not against HEAD~1.
      Comparing to the previous commit answers "what changed last time", which is a
      different and much less useful question than "what changed since a human last
      watched this work in a running game".
    """
    drifted = []
    for f in data["features"]:
        if only and f.get("id") != only:
            continue
        cur, _ = signature(f, working_read)
        rec = f.get("verified_sig")
        if cur is not None and rec is not None and cur != rec:
            drifted.append(f)

    if only and not drifted:
        print(f"{only}: no drift against its blessed signature.")
        return 0
    if not drifted:
        print("no drifted features - nothing to diff.")
        return 0

    rc = 0
    for f in drifted:
        date = f.get("verified_at") or verified_date(f)
        print(f"\n=== {f['name']}  (blessed {date or '?'}) ===")
        sha = _commit_on(date) if date else None
        if not sha:
            # ⚠ Not a clean result, and it must not read like one: with no commit to
            #   compare against, this tool has NOTHING to say about the change.
            print("  ⚠ no commit found on or before that date - cannot diff. The "
                  "signature still\n    says CHANGED; this is a gap in the evidence, "
                  "not an absence of change.")
            rc = 2
            continue
        before = fields(f, commit_read(sha))
        after = fields(f, working_read)
        rows = diff_fields(before, after)
        if not rows:
            # The hash and the field view disagreeing means the signature covers
            # something the field parser does not - whitespace, or a file shape the
            # FIELD regex misses. Say so; a silent "no rows" would read as "no change".
            print("  ⚠ the signature says CHANGED but no FIELD differs. Something the "
                  "hash covers\n    is not being parsed - compare the raw entries by "
                  f"hand:  git diff {sha[:8]} -- {STATS_REL}")
            rc = 2
            continue
        for src, field, old, new in rows:
            if old is None:
                print(f"  + {src}  {field} = {new}"[:160])
            elif new is None:
                print(f"  - {src}  {field}  (was {old})"[:160])
            else:
                print(f"  ~ {src}  {field}"[:160])
                o, n = _focus(old, new)
                print(f"      was: {o}")
                print(f"      now: {n}")
    return rc


def bless_history(data) -> int:
    """Sign each feature as it stood on the day it was verified."""
    for f in data["features"]:
        d = verified_date(f)
        if not d:
            print(f"  skip {f['name']}: no date in its evidence")
            continue
        sha = (_git("rev-list", "-1", f"--before={d} 23:59:59", "HEAD") or "").strip()
        if not sha:
            print(f"  skip {f['name']}: no commit on or before {d}")
            continue
        sig, src = signature(f, commit_read(sha))
        if sig is None:
            print(f"  skip {f['name']}: nothing signable at {sha[:7]}")
            continue
        f["verified_at"] = d
        f["verified_sig"] = sig
        cur, _ = signature(f, working_read)
        flag = "CHANGED" if cur != sig else "unchanged"
        print(f"  {f['name']:<38} {d} {sha[:7]} {sig} {flag}  [{len(src)} parts]")
    save(data)
    print(f"\nwrote {FEATURES}")
    return 0


def bless(data, fid) -> int:
    import datetime
    hits = [f for f in data["features"] if fid in (f.get("id"), f.get("name"))]
    if not hits:
        print(f"no feature {fid!r}", file=sys.stderr)
        return 1
    for f in hits:
        sig, _ = signature(f, working_read)
        f["verified_sig"] = sig
        f["verified_at"] = datetime.date.today().isoformat()
        print(f"blessed {f['name']} -> {sig} @ {f['verified_at']}")
    save(data)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bless-history", action="store_true")
    ap.add_argument("--bless", metavar="ID")
    ap.add_argument("--diff", nargs="?", const=True, metavar="ID",
                    help="say WHAT changed since each drifted feature was blessed")
    a = ap.parse_args()
    d = load()
    if a.bless_history:
        sys.exit(bless_history(d))
    if a.bless:
        sys.exit(bless(d, a.bless))
    if a.diff:
        sys.exit(show_diff(d, None if a.diff is True else a.diff))
    rc = report(d)
    if rc:
        print("\nWhat changed:  py tools/feature_sig.py --diff")
    sys.exit(rc)
