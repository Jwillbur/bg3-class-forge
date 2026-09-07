#!/usr/bin/env python3
"""Project-specific content for the shared build board (vahlok-core/core/scripts/board.py).

    py tools/board_bg3.py --facts    # JSON tiles for the summary strip
    py tools/board_bg3.py --banner   # one sentence: the next action
    py tools/board_bg3.py --panels   # JSON [{id,label,html}] extra tabs

WHY THIS FILE EXISTS
--------------------
The board used to be a 1721-line script welded to this one mod, forked from a 626-line
config-driven engine that Brendan uses for Skyrim. Every improvement since went into the
fork, so his copy silently missed real fixes - including a classifier bug that was
counting 11 of his reference notes as outstanding work.

The engine is now the shared one, and everything BG3-specific lives here. The split is:
the engine knows how to draw tiles, tabs and rows; this file knows what a Warp Die is.
Nothing in here needs to exist in Brendan's copy, and nothing in the engine needs to know
about subclasses.

`--panels` returns raw HTML by design; see `gather_panels` in board.py for the trust
boundary (the command comes from the config, never from the page).
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# --- mod config -----------------------------------------------------------
# Anchored on the MOD BEING AUDITED (the cwd), not on this file. Until
# 2026-09-06 every path below was the literal string CFG.name, so running
# this from another mod silently audited Warpblade instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402
CFG = modconfig.load(Path.cwd())


sys.path.insert(0, str(Path(__file__).resolve().parent))

MOD = CFG.root
ROOT = CFG.root.parent.parent
E = __import__("html").escape


def _run(argv, cwd=None, timeout=300):
    try:
        p = subprocess.run(argv, cwd=cwd or str(MOD), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:                                        # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------ facts ---
def facts() -> dict:
    import dashboard as d          # reuse the collectors rather than reimplement them

    out = {}
    val = d.validator_state()
    # `errors` arrives as a STRING ('0', not 0), so `== 0` is silently always False and a
    # clean validator rendered red. Use the collector's own boolean rather than
    # re-deriving the verdict from text that only looks numeric.
    out["Validator"] = {"value": f"{val['errors']}E / {val['warnings']}W",
                        "sub": f"{val.get('accepted', 0)} accepted",
                        "tone": "good" if val.get("ok") else "bad"}
    rel = d.release_state()
    out["Release"] = {"value": f"{rel['blockers']} blockers",
                      "sub": f"{rel['shoulds']} should-fix",
                      "tone": "good" if rel["blockers"] == 0 else "bad"}
    pak = d.pak_state()
    out["Deployed pak"] = {"value": pak["label"], "sub": pak.get("detail", ""),
                           "tone": "good" if pak.get("ok") else "warn"}

    # DRIFT IS PROMOTED TO THE TOP STRIP, and that is the point of the inversion. When four
    # features had been rewritten after they were verified, the only place that said so was
    # three clicks deep inside the subclass tab. A number nobody sees is not a check.
    F = d.features_state() or {}
    feats = F.get("features", [])
    drift = sum(1 for f in feats if f.get("_drift") == "changed")
    verified = sum(1 for f in feats
                   if f.get("status") == "verified" and f.get("_drift") != "changed")
    out["Features proven"] = {
        "value": f"{verified}/{len(feats)}" if feats else "-",
        "sub": (f"{drift} changed since verified" if drift else "none drifted"),
        "tone": "bad" if drift else "good"}

    # THE BOARD AUDITING ITSELF. Everything else here measures the mod; this measures
    # whether the board's own description of the mod is still true. It runs at page
    # build like the queue audit, because a coherence check you have to remember to run
    # is one that gets run only after someone notices the board is wrong.
    coh = coherence() + reconcile() + doc_freshness()
    if coh:
        kinds = ", ".join(sorted({c["kind"] for c in coh}))
        out["Board coherence"] = {
            "value": f"{len(coh)} off",
            "sub": f"{kinds}", "tone": "bad"}
    else:
        out["Board coherence"] = {"value": "in sync", "sub": "stats, features "
                                  "and docs all agree", "tone": "good"}

    corpus = d.corpus_state()
    out["Corpus"] = {"value": f"{corpus['entries']:,}",
                     "sub": f"{corpus.get('functors', 0)} functors", "tone": "info"}
    return out


# ----------------------------------------------------------------- banner ---
def banner() -> str:
    p = ROOT / "docs" / "context-primer.md"
    if not p.exists():
        return ""
    m = re.search(r"\*\*Next action:([^*]+)\*\*", io.open(p, encoding="utf-8").read())
    return m.group(1).strip(" .—-") if m else ""


# ----------------------------------------------------------------- panels ---
def panels() -> list:
    import dashboard as d
    F = d.features_state()
    if not F or "_error" in F:
        return []
    # The stylesheet ships WITH the markup. Sending html alone is what made this tab
    # render as raw text: the engine styles its own vocabulary (rows, tiles, pills) and
    # knows nothing about a progression grid or a coverage bar.
    css = MOD / "tools" / "subclass_panel.css"
    out = [{"id": "subclass", "label": "Subclass",
            "html": d.subclass_panel(F),
            "css": css.read_text(encoding="utf-8") if css.is_file() else ""}]

    # ⭐ OVERVIEW LEADS, and it carries the app shell with it. The panel's own CSS is
    # injected after the engine's, so `app_shell.css` moves the tab strip into a left
    # sidebar without touching the engine's markup - delete this block and the board
    # reverts exactly.
    #
    # `"lead": True` IS an engine feature, added for this: panels used to sit after the
    # queues unconditionally, and an overview you have to know to click is not an
    # overview. It is opt-in, so every other board - Brendan's included - is unchanged.
    try:
        import overview_panel as ov
        shell = MOD / "tools" / "app_shell.css"
        out.insert(0, {
            "id": "overview", "label": "Overview", "lead": True,
            "html": ov.build(facts(), coherence() + reconcile() + doc_freshness(),
                             _live_passes(), F),
            "css": "\n".join(
                p.read_text(encoding="utf-8") for p in (shell, css) if p.is_file())})
    except Exception as exc:                                    # noqa: BLE001
        # A broken overview must not take the whole board down with it - the other tabs
        # still carry real information. Say so in place rather than rendering nothing.
        out.insert(0, {"id": "overview", "label": "Overview",
                       "html": f"<div class='scpad'><p>overview panel failed: "
                               f"{type(exc).__name__}: {exc}</p></div>", "css": ""})
    return out

# ------------------------------------------------------------- coherence ---
def _shipped_entries() -> dict:
    """{entry name: (type, is_hidden, referenced_by_another_entry)}."""
    out, blobs = {}, {}
    data = MOD / "Public" / CFG.name / "Stats" / "Generated" / "Data"
    for f in sorted(data.glob("*.txt")):
        text = f.read_text(encoding="utf-8", errors="replace")
        for blk in text.split("new entry ")[1:]:
            m = re.match(r'"([^"]+)"', blk)
            # CASE-INSENSITIVE: statuses are shouted (WARPBLADE_SPATIAL_DEBT), and a
            # case-sensitive test hid every one of them from the audit.
            if not m or "warpblade" not in m.group(1).lower():
                continue
            body = blk.split("new entry ")[0]
            kind = re.search(r'^type "([^"]+)"', body, re.M)
            out[m.group(1)] = [kind.group(1) if kind else "?", "IsHidden" in body, False]
            blobs[m.group(1)] = body
    # Reachability: does any OTHER entry name this one?
    for name in out:
        out[name][2] = any(name in b for other, b in blobs.items() if other != name)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def coherence() -> list:
    """Where the board's description of the class disagrees with the shipped class."""
    import json as _json
    fp = MOD / "features.json"
    if not fp.is_file():
        return []
    try:
        feats = _json.loads(fp.read_text(encoding="utf-8")).get("features", [])
    except ValueError:
        return [{"kind": "unreadable", "what": "features.json", "why": "will not parse"}]

    entries = _shipped_entries()
    # A board row is owed for a player-facing PASSIVE or SPELL that is not hidden and is
    # not reached through another entry. Everything else is mechanism.
    # ⚠ REACHABILITY APPLIES TO SPELLS ONLY, and the positive control is what proved it.
    # A PASSIVE is granted by a PROGRESSION, which lives outside the stats, so "is it
    # named by another entry" says nothing about whether a player picks it - and
    # Spatial Repossession IS named by the detonation passives, in a HasPassive() TEST.
    # Excluding it meant deleting a real shipped feature from features.json produced
    # ZERO findings: the audit could not fail in the direction that matters most.
    visible = {n for n, (kind, hidden, referenced) in entries.items()
               if not hidden and (kind == "PassiveData"
                                  or (kind == "SpellData" and not referenced))}
    # Only kinds that CORRESPOND TO A STATS ENTRY can be checked against the stats.
    # `integration` and `issue` rows are verification claims about behaviour ("vanilla
    # subclasses undamaged"), and auditing them reports a phantom for every check the
    # mod has ever passed.
    REAL = {"passive", "spell", "technique", "status", "resource"}
    claimed = {_norm(f.get("name", "")): f for f in feats
               if f.get("name") and f.get("kind") in REAL}
    all_named = {_norm(f.get("name", "")) for f in feats if f.get("name")}
    found = []

    # SHIPPED BUT NOT ON THE BOARD - the failure that actually recurs.
    for name in sorted(visible):
        tail = _norm(re.sub(r"^(Target|Shout|Projectile|Interrupt)_", "", name)
                     .replace("Warpblade_", "").replace("Technique_", ""))
        # matched against EVERY named row, not just the mechanical ones - a shipped
        # entry described by an integration row is still described.
        if not tail or any(tail in k or k in tail for k in all_named):
            continue
        found.append({"kind": "missing", "what": name,
                      "why": "shipped in the stats, but features.json has no feature "
                             "matching it - the board is describing an older class"})

    # ON THE BOARD BUT NOT SHIPPED - what a rename leaves behind.
    allnorm = [_norm(n) for n in entries]
    for key, f in claimed.items():
        if not any(key in n or n in key for n in allnorm):
            found.append({"kind": "phantom", "what": f.get("name", "?"),
                          "why": "features.json claims this, but no stats entry matches "
                                 "it - a rename or a removal left the board behind"})
    return found



# A pass bullet's HEAD carries its verdict. Everything after it is history kept on
# purpose ("Original item follows"), so matching the whole bullet finds the word
# PASSED inside an item that has since reopened. 160 chars covers every real head.
PASS_HEAD = re.compile(r"Pass (\d+[a-z]?)\b")
PASS_DONE = re.compile(r"\bPASSED\b|\bPARTIAL PASS\b|\bPARTLY PASSED\b|\bCLOSED\b", re.I)
PASS_OPEN = re.compile(r"\bOPEN\b|\bRETEST\b|\bFAILED\b|\bREQUIRED\b", re.I)


def _live_passes() -> dict:
    """Every live pass the queue defines -> (verdict, the head that says so).

    Reads the queue as the RECORD OF WHAT HAPPENED, which is what it is. The statuses
    in features.json are a second, hand-kept copy of the same claim, and two hand-kept
    copies of one fact drift the moment a result lands in one of them.
    """
    fp = ROOT / "docs" / "work-live.md"
    if not fp.is_file():
        return {}
    seen = {}
    for raw in fp.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("- **"):
            continue
        head = line[:160]
        m = PASS_HEAD.search(head)
        if not m:
            continue
        num = m.group(1)
        # A HEAD CAN BE BOTH, and picking one throws the other away. "PARTIAL PASS
        # ... retest the second half" is a recorded result AND an open half in one
        # sentence: the recorded half is what a status of "untested" contradicts, and
        # the open half is what a status of "verified" contradicts. The positive
        # control is what proved this - with a single verdict, the word "passed" in a
        # renumbering note was enough to make an open pass read as finished.
        verdict = set()
        if PASS_DONE.search(head):
            verdict.add("done")
        if PASS_OPEN.search(head):
            verdict.add("open")
        seen.setdefault(num, []).append((verdict or {"unclear"}, head))
    return seen


def reconcile() -> list:
    """Where the Subclass tab and the Live queue tell different stories.

    ⭐ WHY THIS EXISTS, and why coherence() did not already cover it: coherence()
    compares features.json to the SHIPPED STATS - it answers "does this feature exist".
    doc_freshness() compares docs to COMMITS - it answers "has the source moved since".
    Neither can see the failure that actually kept recurring: a live test runs, its
    result is written into work-live.md, and features.json - the file the Subclass tab
    renders - is never touched. Passes 24 and 25 both recorded results on 2026-08-23
    and the tab still read "untested" two days later, through a full wrapup and a
    coherence check that reported "in sync". Both files were internally consistent.
    They just disagreed with each other, and nothing was looking at the seam.
    """
    import json as _json
    fp = MOD / "features.json"
    if not fp.is_file():
        return []
    try:
        feats = _json.loads(fp.read_text(encoding="utf-8")).get("features", [])
    except ValueError:
        return []
    passes = _live_passes()
    out = []

    # ONE NUMBER, TWO ITEMS. Pass 23 meant both a passed VFX test and a twice-failed
    # capstone at the same time, in the same board, one green and one red.
    for num, rows in sorted(passes.items(), key=lambda kv: int(kv[0].rstrip("abc") or 0)):
        if len(rows) > 1:
            out.append({"kind": "duplicate pass", "what": f"Pass {num}",
                        "why": f"{len(rows)} different queue items are both called "
                               f"Pass {num} - the number resolves to two things"})

    for f in feats:
        name, status = f.get("name", "?"), f.get("status")
        named = PASS_HEAD.findall(f.get("pass", "") or "")
        verdicts = {v for n in named for vs, _ in passes.get(n, []) for v in vs}

        # A RESULT LANDED AND THE BOARD NEVER ABSORBED IT.
        if status == "untested" and "done" in verdicts:
            out.append({"kind": "unabsorbed result", "what": name,
                        "why": f"the queue records a result for {f.get('pass')}, but the "
                               f"Subclass tab still calls this untested"})

        # A GREEN LIGHT OVER AN OPEN PASS.
        if status == "verified" and "open" in verdicts:
            out.append({"kind": "green over open", "what": name,
                        "why": f"marked verified, but {f.get('pass')} is still open or "
                               f"awaiting a retest in the live queue"})

        # features.json's own stated rule, enforced instead of just written down:
        # "every non-verified feature must name the live pass that would settle it."
        if status not in ("verified", "pinned", "accepted") and not named:
            out.append({"kind": "no pass named", "what": name,
                        "why": "not verified and names no live pass - features.json's "
                               "own editing rules require one"})

        # A pass number that is not in the queue at all - but ONLY for a feature that
        # still needs one. The queue's own convention is to STRIKE a pass once it is
        # done, so a verified feature citing a struck pass is correct housekeeping, not
        # a defect; flagging those lit up 8 rows that were all fine, and a tile that
        # cries wolf on routine work is one you learn to dismiss. For an UNVERIFIED
        # feature the same citation is a dead end: nothing queued will ever settle it.
        if status != "verified":
            for n in named:
                if n not in passes:
                    out.append({"kind": "dangling pass", "what": name,
                                "why": f"cites Pass {n}, which work-live.md does not "
                                       f"define - nothing queued will settle this"})
    return out


def doc_freshness() -> list:
    """Docs the board RENDERS that predate the mod source they describe.

    Commits, not mtimes - a checkout or an editor touch moves an mtime, but "four commits
    have changed the class since this doc was written" is unambiguous and is exactly the
    sentence a stale board needs to say about itself.
    """
    watched = [
        ("docs/context-primer.md", "the Next action banner"),
        ("docs/work-live.md", "the Live queue tab"),
        ("docs/work-offline.md", "the Offline queue tab"),
    ]
    # ⚠ Mods/ is DELIBERATELY EXCLUDED. It holds meta.lsx - a version bump changes
    # nothing a queue item could be wrong about, and counting it meant every release
    # flagged all three docs as behind. A tile that cries wolf on routine work is one
    # you learn to dismiss, which is the same failure as no tile at all.
    src = ["bg3/Warpblade/Public", "bg3/Warpblade/Localization"]
    out = []
    for rel, what in watched:
        code, sha = _run(["git", "log", "-1", "--format=%H", "--", rel], cwd=str(ROOT))
        sha = sha.strip()
        if code != 0 or not sha:
            continue
        code, after = _run(["git", "log", "--oneline", f"{sha}..HEAD", "--", *src],
                           cwd=str(ROOT))
        n = len([x for x in after.splitlines() if x.strip()]) if code == 0 else 0
        if n:
            out.append({"kind": f"{n} commit(s) behind", "what": what,
                        "why": f"{rel} has not been touched since the mod source moved "
                               f"{n} time(s) - it is describing an older build"})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", action="store_true")
    ap.add_argument("--banner", action="store_true")
    ap.add_argument("--panels", action="store_true")
    a = ap.parse_args()
    if a.facts:
        print(json.dumps(facts()))
    elif a.banner:
        print(banner())
    elif a.panels:
        print(json.dumps(panels()))
    else:
        ap.error("pick one of --facts / --banner / --panels")
