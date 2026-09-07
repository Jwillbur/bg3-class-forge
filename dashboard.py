"""
Generate the Warpblade build board.

Reads the ACTUAL repo state - the work queues, the validator, the release audit, the
Osiris logs, the built pak, git - and emits a single self-contained interactive page.
Nothing here is hand-maintained, so the board cannot drift from reality the way a
hand-written status page does.

    py dashboard.py            # write dashboard.html
    py dashboard.py --open     # ...and open it in the default browser
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import subprocess
import xml.etree.ElementTree as ET
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

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
ROOT = CFG.root.parent.parent
TOOLS = MOD / "tools"
PUBLIC = MOD / "Public/Warpblade"
OUT = MOD / "dashboard.html"

LIVE = ROOT / "docs" / "work-live.md"
OFFLINE = ROOT / "docs" / "work-offline.md"
REFDOC = ROOT / "docs" / "bg3-subclass-headless-reference.md"

sys.path.insert(0, str(TOOLS))


# ----------------------------------------------------------------- helpers ---
def run(cmd, cwd=None, timeout=240):
    try:
        p = subprocess.run(cmd, cwd=cwd or str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:                                    # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def strip_md(s: str) -> str:
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\*\*([^*]*)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]*)\*", r"\1", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    return s.strip()


MARKS = {"✅": "done", "⚠": "attention", "⏸": "pinned", "⭐": "starred",
         # 📖 and 🔁 were already being written into the queues as "REFERENCE, not a
         # task" and "STANDING HABIT" markers, but no glyph mapped to "note", so six rows
         # that say in their own first line that they are not tasks were counted as OPEN.
         "📖": "note", "🔁": "note"}


def classify(text: str) -> str:
    for gly, name in MARKS.items():
        if gly in text[:6]:
            return name
    lead = text[:90]
    # PASSED/PASSES added 2026-08-19: four live rows were written as "[PASSED] Pass 11c"
    # instead of the checkmark glyph the others use, and since the glyph was the only
    # thing marking a live pass done, all four sat in the board as OPEN. The rows were
    # corrected, but the word itself now counts too - a live pass is the one queue whose
    # done-word is "passed", and it was the only done-word missing from this list.
    if re.search(r"\b(SUPERSEDED|CLOSED|RESOLVED|DONE|RE-FIXED|FIXED|UPGRADED|REWIRED|REVERTED|"
                 r"PASSED|PASSES)\b", lead):
        return "done"
    if re.search(r"\bDOWNGRADED\b", lead):
        return "low"
    if re.search(r"standing habit|workflow rule|worth reusing|Deliberately NOT|LEAD ONLY|"
                 r"not applied|Known structural limits|reference, not a task|"
                 r"on file, not a task|watch, not a task|not as a hold",
                 # case-insensitive since 2026-08-19: every one of these phrases is written
                 # in CAPS in the real queues ("STANDING HABIT", "REFERENCE, not a task"),
                 # so the lowercase patterns matched none of the rows they were written for.
                 text[:220], re.I):
        return "note"
    return "open"


def clip(s: str, cap=150) -> str:
    s = re.sub(r"^[✅⚠⏸⭐❌→📖🔁?\s]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    m = re.match(r"(.{40,%d}?[.!?])\s" % cap, s)
    return (m.group(1) if m else s[:cap]).strip()


# -------------------------------------------------------------- collectors --
def parse_queue(path: Path):
    """Sub-bullets of the BG3-1 block only - the file also holds Skyrim workstreams."""
    if not path.exists():
        return []
    lines = io.open(path, encoding="utf-8").read().split("\n")
    start = next((i for i, l in enumerate(lines) if l.startswith("BG3-1.")), None)
    if start is None:
        return []
    items = []
    for line in lines[start + 1:]:
        if re.match(r"^[A-Za-z0-9][A-Za-z0-9-]*\.\s", line):
            break
        m = re.match(r"^\s{2,}- \*\*(.+)$", line)
        if not m:
            continue
        raw = strip_md(m.group(1))
        if not raw:
            continue
        # Trackable items only: a status glyph or a named Pass. The rest is standing
        # procedure ("Setup: launch BG3") and would inflate the open count.
        if not (re.search(r"[✅⚠⏸⭐❌]", raw[:6]) or re.search(r"\bPass \d", raw[:40])):
            continue
        items.append({"status": classify(raw), "title": clip(raw), "full": raw})
    return items


def parse_offline(path: Path):
    if not path.exists():
        return []
    lines = io.open(path, encoding="utf-8").read().split("\n")
    start = next((i for i, l in enumerate(lines) if l.startswith("**BG3-0.")), None)
    if start is None:
        return []
    out = []
    for line in lines[start + 1:]:
        if re.match(r"^\*\*[A-Za-z0-9][A-Za-z0-9-]*\.", line):
            break
        m = re.match(r"^(\d+)\. \*\*(.+)$", line)
        if not m:
            continue
        raw = strip_md(m.group(2))
        out.append({"n": int(m.group(1)), "status": classify(raw),
                    "title": clip(raw), "full": raw})
    return out


def validator_state():
    code, out = run([sys.executable, str(TOOLS / "validate.py")], cwd=str(TOOLS))
    findings = [{"grade": g.group(1), "where": g.group(2).strip(),
                 "message": g.group(3).strip()}
                for g in re.finditer(r"^(ERROR|WARN)\s+(.+)\n\s+(.+)$", out, re.M)]
    m = re.search(r"(\d+) error\(s\), (\d+) warning\(s\)(?:, (\d+) accepted)?", out)
    if not m:
        return {"ok": False, "errors": "?", "warnings": "?", "accepted": "0",
                "findings": findings}
    e, w, a = m.group(1), m.group(2), m.group(3) or "0"
    return {"ok": code == 0 and e == "0", "errors": e, "warnings": w,
            "accepted": a, "findings": findings}


def release_state():
    _, out = run([sys.executable, str(TOOLS / "release_check.py"), "--json"], cwd=str(TOOLS))
    try:
        data = json.loads(out[out.find("{"):])
    except ValueError:
        return {"blockers": 0, "shoulds": 0, "notes": 0, "findings": [],
                "module": {}, "version": "?"}
    f = data.get("findings", [])
    return {"blockers": sum(1 for x in f if x["grade"] == "BLOCKER"),
            "shoulds": sum(1 for x in f if x["grade"] == "SHOULD"),
            "notes": sum(1 for x in f if x["grade"] == "NOTE"),
            "findings": f, "module": data.get("module", {}),
            "version": data.get("version", "?")}


SHORT_STAGE = {"StartedPreviewingSpell": "preview", "StoppedPreviewingSpell": "stopped",
               "UsingSpell": "using", "UsingSpellOnTarget": "onTarget",
               "CastSpell": "cast", "CastedSpell": "casted"}


def triage_state():
    """Last play session's spell verdicts, via triage_log's own collector."""
    try:
        import triage_log as tl
        path = tl.newest("Osiris Runtime*.log")
        if not path:
            return {"log": None, "spells": [], "statuses": []}
        spells, statuses = tl.collect(path, None)
        rows = []
        for sid in sorted(spells):
            v = spells[sid]
            stages = " ".join(f"{SHORT_STAGE.get(s, s)}={v['stages'][s]}"
                              for s in tl.PIPELINE if v["stages"].get(s))
            rows.append({"id": sid, "verdict": tl.verdict(v), "stages": stages,
                         "failed": v["failed"]})
        stat = [{"id": k, "applied": v["count"], "removed": v["removed"]}
                for k, v in sorted(statuses.items())]
        return {"log": path.name,
                "when": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "spells": rows, "statuses": stat}
    except Exception as exc:                                     # noqa: BLE001
        return {"log": None, "spells": [], "statuses": [], "error": str(exc)}


def pak_state():
    pak, rep = MOD / "dist" / "Warpblade.pak", MOD / "dist" / "_deploy_report.txt"
    if not pak.exists():
        return {"ok": False, "label": "never built", "detail": "run build.ps1"}
    pmt = pak.stat().st_mtime
    newest, nmt = None, 0.0
    for sub in ("Public", "Mods", "Localization"):
        d = MOD / sub
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.is_file() and f.suffix.lower() != ".loca" and f.stat().st_mtime > nmt:
                newest, nmt = f, f.stat().st_mtime
    if nmt > pmt:
        return {"ok": False, "label": "stale", "detail": f"{newest.name} is newer"}
    if not rep.exists():
        return {"ok": False, "label": "not deployed", "detail": "no deploy report"}
    txt = io.open(rep, encoding="utf-8", errors="replace").read()
    m = re.search(r"(\d+)\s+Warpblade\.pak", txt)
    if not m or int(m.group(1)) != pak.stat().st_size:
        return {"ok": False, "label": "deploy mismatch", "detail": "game has another build"}
    age = time.time() - pmt
    when = ("just now" if age < 300 else f"{int(age // 60)}m ago" if age < 3600
            else f"{int(age // 3600)}h ago" if age < 86400 else f"{int(age // 86400)}d ago")
    return {"ok": True, "label": "current",
            "detail": f"built {when}, {pak.stat().st_size // 1024:,} KB"}


def corpus_state():
    idx, voc = MOD / "corpus/index.json", MOD / "corpus/vocabulary.json"
    d = {"entries": 0, "functors": 0, "conditions": 0, "enums": 0}
    if idx.exists():
        d["entries"] = len(json.loads(io.open(idx, encoding="utf-8").read()))
    if voc.exists():
        v = json.loads(io.open(voc, encoding="utf-8").read())
        d.update(functors=len(v.get("functors", [])),
                 conditions=len(v.get("conditions", [])),
                 enums=len(v.get("valuelists", {})))
    return d


def mod_state():
    stats = CFG.stats
    entries = sum(len(re.findall(r"^new entry ",
                                 io.open(p, encoding="utf-8", errors="replace").read(), re.M))
                  for p in stats.glob("*.txt"))
    secs = (len(re.findall(r"^## \d+\.", io.open(REFDOC, encoding="utf-8").read(), re.M))
            if REFDOC.exists() else 0)
    return {"entries": entries, "sections": secs}


def meta_version() -> str:
    """The MOD's shipped version, from meta.lsx.

    Parsed as XML and scoped to the ModuleInfo node, not grepped. meta.lsx carries a
    Version64 for every DEPENDENCY as well as for the mod, and the first match in the
    file is CommunityLibrary's - a plain regex here reported the design version as
    "v2.3" on the first attempt.
    """
    p = MOD / "Mods" / CFG.name / "meta.lsx"
    try:
        root = ET.parse(p).getroot()
    except (OSError, ET.ParseError):
        return ""
    info = root.find(".//node[@id='ModuleInfo']")
    if info is None:
        return ""
    a = info.find("attribute[@id='Version64']")
    if a is None or not (a.get("value") or "").isdigit():
        return ""
    v = int(a.get("value"))
    return f"{v >> 55}.{(v >> 47) & 0xFF}.{(v >> 31) & 0xFFFF}.{v & 0x7FFFFFFF}"


def git_log(n=8):
    code, out = run(["git", "log", f"-{n}", "--pretty=%h\x1f%ar\x1f%s"])
    if code != 0:
        return []
    rows = []
    for line in out.strip().split("\n"):
        p = line.split("\x1f")
        if len(p) == 3:
            rows.append({"sha": p[0], "when": p[1], "subject": p[2]})
    return rows


def next_action():
    p = ROOT / "docs" / "context-primer.md"
    if not p.exists():
        return "See docs/context-primer.md"
    m = re.search(r"\*\*Next action:([^*]+)\*\*", io.open(p, encoding="utf-8").read())
    return strip_md(m.group(1)).strip(" .") if m else "See docs/context-primer.md"


# (module, tagline, description, allowlist key or None)
TOOLS_INFO = [
    ("validate.py", "Blocks a broken build",
     "Eleven checks against the shipped game data: invented functors, functors used in a "
     "field vanilla never uses, dangling references, malformed SpellAnimation, UUID wiring, "
     "and whether the deployed pak is stale. Runs as step 0 of build.ps1 and refuses to pack "
     "on any ERROR.", "validate"),
    ("release_check.py", "Is it publishable",
     "Audits what a mod browser shows: author, tags, info.json, dependencies, localisation, "
     "icon encoding. Generates dist/info.json during every build so it cannot go stale.",
     "release"),
    ("icon_atlas.py", "RETIRED - refuses to write",
     "The recoloured-atlas experiment, reverted at v1.5.4 after the art was judged bad in "
     "game. It is kept because the pipeline still works and the finding is worth having, but "
     "it now REFUSES to run: it writes the same three atlas paths under the same UUID as "
     "feature_icons.py, so re-running it would silently swap the shipping atlas for the "
     "reverted one with every check staying green - the replacement is itself a valid atlas. "
     "The button runs --names, which only lists what it would touch.", "icons"),
    ("feature_icons.py", "The third icon system",
     "Owns PassiveData/SpellData `data \"Icon\"` art - the character sheet, the level-up "
     "feature list and every tooltip. That is a separate system from the resource bar and "
     "the class emblem, which is why an atlas alone never fixed a tooltip: Game.pak ships "
     "1521 loose 380x380 tooltip icons and the tooltip surface reads the loose file. Writes "
     "four sizes plus the atlas; --check fails if any Warpblade_* icon name lacks art.",
     None),
    ("make_die.py", "The Warp Dice emblem",
     "Renders the resource icon as real geometry - a pentagonal trapezohedron, ten kite "
     "faces - flat-shaded from true face normals, with the class sword lifted out of the "
     "class icon and punched through it. Replaced a d20 showing \"10\", a die this class "
     "never rolls, on a schedule (d8 to Fighter 9, d10 after) where no fixed number is right.",
     None),
    ("strip_ring.py", "Hotbar art from the class icon",
     "Derives art/hotbar icon.png from art/class icon.png by removing the brass ring and the "
     "violet field, which is what Larian does - Game.pak's hotbar pair is the ringed emblem "
     "with its ring stripped. Neither colour nor radius can make that cut (the ring is brass "
     "like the crossguard, and the guard tips overlap it); connected components can.", None),
    ("anim_textkeys.py", "What the clips actually fire on",
     "Indexes the text keys carried by the animation clips our spells play, filtered to the "
     "ten playable races and resolving `using` inheritance. fx_audit reads it to prove every "
     "StartTextKey exists - the check that would have caught four effects keyed to events "
     "their clip never had, which took three live reports to find.", None),
    ("feature_sig.py", "Is the green light still earned",
     "Signs the entries and mod-shipped MEIs behind each feature, so the board can say when "
     "an implementation changed AFTER it was verified. Status stays on the author's honour; "
     "staleness does not. Recorded signatures are recovered from git at each feature's own "
     "verification date rather than blessed at today's state.", None),
    ("balance_sim.py", "Is the maths defensible",
     "Monte-Carlo action economy against Champion and Battle Master, reading the real dice "
     "pool and level maps out of the mod rather than remembering them. Built to settle the "
     "6-vs-8 dice question with counting instead of feel.", "balance"),
    ("loca_lint.py", "Is the text itself right",
     "Lints the localisation file - the half nothing else checked. The validator only asked "
     "whether a handle EXISTS; this asks whether the sentence is right: raw vs escaped LSTags, "
     "LSTag targets that resolve, placeholder arity, orphans, duplicates. Every threshold is "
     "measured against the shipped game and printed with the finding.", "loca"),
    ("inherit_audit.py", "What `using` gave you for free",
     "Lists every field inherited through a `using` chain that the entry never overrides, "
     "resolving loca handles to the sentence the player actually reads. Built after two "
     "tooltip bugs in one day came from inherited text that was true of the parent and "
     "false of the child.", "inherit"),
    ("fx_audit.py", "Does it look and sound like anything",
     "Audits every VFX and SFX field against the shipped game. Two failure modes: naming a "
     "sound event or effect GUID nothing defines (silence, no error), and naming nothing at "
     "all, where `using` quietly fills the field with someone else's flavour - Warped Blade, "
     "a Force cantrip, was casting on a mundane sword swing. Thresholds are what vanilla of "
     "the same SpellType actually carries, and `// FXMODEL:` names the one entry a spell is "
     "styled after so it is judged against that rather than an average.", "fx"),
    ("conflict_probe.py", "Does it clash with their mod",
     "Probes every other installed mod for real collisions: shared stats entry names, shared "
     "loca handles, and GUIDs both mods claim as their OWN identity. Two mods pointing at the "
     "same vanilla asset is not a conflict, and the probe says so instead of reporting it.",
     "conflict"),
    ("triage_log.py", "What happened in game",
     "Streams the Osiris log and reports each spell's furthest cast-pipeline stage, plus "
     "status applications. Cannot see interrupts - they are not logged at all.", "triage"),
    ("corpus_index.py", "Ground truth, queryable",
     "Indexes every entry in the extracted game data and mines the mechanism vocabulary. "
     "Run --find <Functor> before writing any call: zero hits means you are inventing one.",
     "corpus"),
    ("corpus_report.py", "The readable artifact",
     "Renders the mined vocabulary to corpus/MECHANISMS.md, tracked in git so a game patch "
     "shows up as a reviewable diff.", "corpus_report"),
    ("build.ps1 -SkipPack", "Preflight only",
     "Validator plus the XML and localisation-handle checks, without packing or deploying.",
     "build_check"),
    ("build.ps1", "Full build + deploy",
     "Validate, compile localisation, pack, regenerate info.json, and deploy outside the "
     "MSIX container with a verified report.", "build"),
    ("dashboard.py", "This page",
     "Runs the tools above and renders their current output. Regenerate any time.", None),
]


# -------------------------------------------------------------------- html --
E = html.escape


def features_state():
    """Load features.json - the hand-maintained subclass breakdown.

    Deliberately not derived. No static check can know whether a feature behaves
    correctly in play, and a green light nobody earned is exactly how three
    unverified features ended up described as working in the README.
    """
    path = MOD / "features.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"_error": str(exc)}

    # STATUS is on the author's honour; STALENESS is not, and staleness is the half that
    # rotted. "verified" is a claim about a MOMENT, and nothing was watching the
    # implementation after that moment - Warp Assault stayed green through three rewrites
    # of its cast effect. feature_sig hashes the entries and mod-shipped MEIs behind each
    # feature (comments stripped, `using` parents followed), so the board can say "what
    # was proven is not what is shipping" without pretending to know whether it works.
    try:
        import feature_sig
        for f in data.get("features", []):
            cur, _ = feature_sig.signature(f, feature_sig.working_read)
            rec = f.get("verified_sig")
            f["_drift"] = ("changed" if (cur and rec and cur != rec)
                           else "clean" if (cur and rec) else "")
    except Exception:
        # ⛔ NOT a silent pass. The board must not go down for a drift check - that
        #   part was right - but leaving _drift unset renders as the SAME blank as
        #   "no drift", so a broken check read as a clean one. Say unknown instead.
        for f in data.get("features", []):
            f.setdefault("_drift", "unknown")
    return data


# Every "Send to Claude" button carries this preamble.
#
# It exists because of a real failure: the button used to emit a DESCRIPTION of the
# row, so a click on the pinned Warp Assault animation arrived looking like a
# statement of fact rather than an instruction, and it was treated as ambiguous
# instead of acted on. The user's correction was exact - Claude wrote that prompt
# text itself, so reading it back as ambiguous evidence is circular. The click is
# the instruction, full stop.
CLAUDE_TASK_PREAMBLE = (
    "TASK - work this now and resolve it. I clicked Send to Claude on this row, "
    "which IS my authorisation to act on it: do not ask me whether I meant it, and "
    "do not treat any 'pinned', 'deferred', 'do not touch' or 'needs a decision' "
    "note below as a reason to stop - clicking it is me lifting that. Research "
    "first if the answer is not already known, then make the change, verify it, "
    "and report what you did. Item follows."
)

# Order features are shown in, and how they group.
KIND_GROUPS = [
    ("Core progression", ("resource", "spell", "passive", "issue")),
    ("Warp Techniques", ("technique",)),
    ("Integration", ("integration",)),
]
# Statuses in the order they appear on the coverage bar: best to worst.
STATUS_ORDER = ["verified", "accepted", "partial", "fixed-unproven", "untested",
                "needs-fix", "pinned"]
# "accepted" sits second because it is a real outcome, not a lesser "verified": the user
# looked at it and closed it. It is separate because the evidence field for the Warp Assault
# animation said in as many words "this is acceptance, NOT confirmation" while the row was
# tagged verified - the vocabulary had no word for the state the entry was actually in.


def technique_picks() -> dict[int, int]:
    """How many Warp Techniques you choose at each level, read from the mod.

    Parsed from Progressions.lsx rather than written down here, so it cannot drift
    from what the progression actually grants. Returns {fighter_level: count}.
    """
    path = PUBLIC / "Progressions/Progressions.lsx"
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    out: dict[int, int] = {}
    # Split on the node boundary so a row's Level and Selectors cannot be paired
    # with a neighbour's - the same trap that made an earlier edit drop the wrong
    # Warp Die grant.
    for blk in re.split(r'(?=<node id="Progression">)', text):
        sel = re.search(r'SelectPassives\([^,]+,\s*(\d+)\s*,\s*Warpblade_Techniques\)', blk)
        lvl = re.search(r'id="Level"[^>]*value="(\d+)"', blk)
        if sel and lvl:
            out[int(lvl.group(1))] = out.get(int(lvl.group(1)), 0) + int(sel.group(1))
    return out


def subclass_panel(F) -> str:
    """Render the Subclass tab: what the mod ships, and what is actually proven."""
    if F is None:
        return ('<div class="ph"><h2>Subclass</h2></div>'
                '<div class="scpad"><p class="muted">No <code>features.json</code>. '
                'That file is the source for this tab.</p></div>')
    if "_error" in F:
        return ('<div class="ph"><h2>Subclass</h2></div>'
                f'<div class="scpad"><p class="muted">features.json will not parse: '
                f'{E(F["_error"])}</p></div>')

    feats = F.get("features", [])
    statuses = F.get("statuses", {})
    counts = Counter(f.get("status", "untested") for f in feats)
    total = max(len(feats), 1)

    def tone_of(st):
        return statuses.get(st, {}).get("tone", "idle")

    def label_of(st):
        return statuses.get(st, {}).get("label", st)

    # ---- coverage bar ------------------------------------------------------
    segs = []
    for st in STATUS_ORDER:
        n = counts.get(st, 0)
        if not n:
            continue
        pct = 100.0 * n / total
        segs.append(f'<div class="seg t-{tone_of(st)}" style="width:{pct:.4f}%" '
                    f'title="{E(label_of(st))}: {n}"></div>')
    bar = '<div class="cbar">' + "".join(segs) + "</div>"

    legend = "".join(
        f'<span class="lg"><i class="dot t-{tone_of(st)}"></i>{E(label_of(st))}'
        f'<b>{counts.get(st, 0)}</b></span>'
        for st in STATUS_ORDER if counts.get(st, 0)
    )

    # THE HEADLINE NUMBER EXCLUDES DRIFT. A feature whose files changed after it was
    # verified is not a verified feature, and this is the one number a reader takes away
    # from the tab - it said "18 verified (95%)" while four of the eighteen had been
    # rewritten since anyone looked at them.
    drifted = sum(1 for f in feats if f.get("_drift") == "changed")
    verified = sum(1 for f in feats
                   if f.get("status") == "verified" and f.get("_drift") != "changed")
    proven_pct = 100.0 * verified / total

    # ---- what one launch would buy ----------------------------------------
    # Group everything unproven by the pass that would settle it. The pass
    # covering the most features is the highest-value single launch.
    by_pass = defaultdict(list)
    for f in feats:
        # "accepted" joins these: the user closed it deliberately, so proposing a launch
        # to re-settle it would be advertising work nobody asked for.
        #
        # DRIFT OVERRIDES ALL OF THAT. If the files behind a feature changed after it was
        # verified, it is unproven again whatever its badge says - and this panel used to
        # render its heading and intro over an EMPTY list precisely because every row was
        # nominally green while four of them had been rewritten underneath.
        if f.get("_drift") != "changed" and f.get("status") in ("verified", "accepted", "pinned"):
            continue
        p = ("re-verify: changed since it was proven"
             if f.get("_drift") == "changed" else (f.get("pass") or "unassigned"))
        by_pass[p].append(f["name"])
    ranked = sorted(by_pass.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    launch_rows = "".join(
        f'<li class="lrow"><span class="pass">{E(p)}</span>'
        f'<span class="lcount">{len(names)}</span>'
        f'<span class="lnames">{E(", ".join(names))}</span></li>'
        for p, names in ranked
    )

    # ---- publish gate ------------------------------------------------------
    # Features the README describes as working that are not verified. This is the
    # honest gap between "release_check says 0 blockers" and "it actually works".
    gate = [f for f in feats
            if (f.get("status") in ("fixed-unproven", "needs-fix", "partial")
                or f.get("_drift") == "changed")
            and f.get("kind") in ("technique", "spell", "passive")]
    gate_html = "".join(
        f'<li class="grow"><span class="spill t-{"warn" if f.get("_drift") == "changed" else tone_of(f["status"])}">'
        f'{E("changed since verified" if f.get("_drift") == "changed" else label_of(f["status"]))}</span>'
        f'<span class="gname">{E(f["name"])}</span>'
        f'<span class="gpass">{E(f.get("pass", ""))}</span></li>'
        for f in sorted(gate, key=lambda x: STATUS_ORDER.index(x.get("status", "untested")),
                        reverse=True)
    )

    # ---- level map ---------------------------------------------------------
    dice_by_level = {d["level"]: d for d in F.get("dice", {}).get("by_level", [])}
    picks = technique_picks()
    n_techniques = sum(1 for f in feats if f.get("kind") == "technique")

    unlocks = defaultdict(list)
    for f in feats:
        # Techniques are deliberately NOT chipped per level. Every one of them
        # carries level 3, so listing them here claimed you get all seven at
        # Fighter 3 when you actually choose two. The "choose N" badge below is
        # the honest version; the full list lives in the feature table.
        if f.get("kind") in ("issue", "integration", "technique"):
            continue
        unlocks[f.get("level", 0)].append(f)

    lvl_cells, known = [], 0
    for lv in sorted(set(list(dice_by_level) + list(unlocks) + list(picks))):
        d = dice_by_level.get(lv)
        got = unlocks.get(lv, [])
        chips = "".join(
            f'<span class="uch t-{tone_of(f["status"])}" title="{E(label_of(f["status"]))}">'
            f'{E(f["name"])}</span>' for f in got)
        dice_txt = (f'{d["dice"]}<span class="die">{E(d["die"])}</span>' if d else "&mdash;")

        pick = picks.get(lv, 0)
        badge = ""
        if pick:
            known += pick
            badge = (f'<div class="pick"><b>+{pick}</b> Warp Technique'
                     f'{"s" if pick > 1 else ""}'
                     f'<span class="pickrun">{known} of {n_techniques} known</span></div>')

        body = chips + badge
        lvl_cells.append(
            f'<div class="lvl{" haspick" if pick else ""}">'
            f'<div class="lvn">Fighter {lv}</div>'
            f'<div class="lvd">{dice_txt}</div>'
            f'<div class="lvu">{body or "<span class=\'muted\'>&mdash;</span>"}</div></div>')
    level_map = '<div class="lvlmap">' + "".join(lvl_cells) + "</div>"

    # ---- action economy ----------------------------------------------------
    econ = defaultdict(list)
    for f in feats:
        a = f.get("action", "-")
        if a in ("-", ""):
            continue
        econ[a].append(f)
    econ_html = "".join(
        '<div class="ecol"><div class="ecl">' + E(a) + '</div>' +
        "".join(f'<div class="eit t-{tone_of(f["status"])}">{E(f["name"])}'
                f'<span class="ecost">{E(f.get("cost", "-"))}</span></div>'
                for f in items) + '</div>'
        for a, items in sorted(econ.items(), key=lambda kv: -len(kv[1]))
    )

    # ---- the feature table -------------------------------------------------
    groups = []
    for gname, kinds in KIND_GROUPS:
        rows = [f for f in feats if f.get("kind") in kinds]
        if not rows:
            continue
        rows.sort(key=lambda f: (f.get("level", 0),
                                 STATUS_ORDER.index(f.get("status", "untested"))))
        body = ""
        for f in rows:
            st = f.get("status", "untested")
            flags = "".join(f'<span class="flag">{E(x)}</span>'
                            for x in f.get("flags", []))
            rng = f' &middot; {E(f["range"])}' if f.get("range") else ""
            detail = ""
            if f.get("evidence") or f.get("note"):
                detail = ('<div class="scdetail">'
                          + (f'<p><b>Evidence:</b> {E(f["evidence"])}</p>'
                             if f.get("evidence") else "")
                          + (f'<p><b>Watch for:</b> {E(f["note"])}</p>'
                             if f.get("note") else "")
                          + '</div>')
            prompt = (f'{CLAUDE_TASK_PREAMBLE}\n\n'
                      f'Warpblade feature: {f["name"]} '
                      f'(level {f.get("level", "?")}, status: {label_of(st)}, '
                      f'covered by {f.get("pass", "no pass")}).\n'
                      f'Evidence so far: {f.get("evidence", "none")}\n'
                      f'Notes: {f.get("note", "none")}')
            btn = (f'<button class="send" data-prompt="{E(prompt)}">Send to Claude</button>')
            if f.get("_drift") == "changed":
                flags = (f'<span class="flag drift" title="The files behind this '
                         f'feature changed after it was verified on '
                         f'{E(f.get("verified_at","?"))}. It has not been re-proven.">'
                         f'changed since verified</span>' + flags)
            body += (
                f'<li class="screw t-{tone_of(st)}" data-st="{E(st)}" tabindex="0" data-expand>'
                f'<span class="spill t-{tone_of(st)}">{E(label_of(st))}</span>'
                f'<div class="scmain">'
                f'<div class="scline"><span class="scname">{E(f["name"])}</span>{flags}'
                f'{btn}</div>'
                f'<div class="scmeta">L{f.get("level", "?")} &middot; '
                f'{E(f.get("action", "-"))} &middot; {E(f.get("cost", "-"))}{rng}'
                f' &middot; <span class="pass">{E(f.get("pass", "-"))}</span></div>'
                f'{detail}</div></li>')
        groups.append(f'<div class="scgroup"><h3>{E(gname)}</h3><ul>{body}</ul></div>')

    legend_full = "".join(
        f'<div class="lgrow"><span class="spill t-{tone_of(st)}">{E(label_of(st))}</span>'
        f'<span class="lgd">{E(statuses[st].get("desc", ""))}</span></div>'
        for st in STATUS_ORDER if st in statuses)

    # BOTH OF THESE USED TO BE HAND-TYPED FIELDS IN features.json, AND BOTH HAD DRIFTED.
    # The header read "v1.6" while the mod shipped v1.7.7.0, and "updated 2026-08-19"
    # while five releases had landed since. A number a human has to remember to change is
    # a number that will be wrong, so neither is written down any more: the design version
    # is the shipped version's major.minor, and the date is features.json's own last
    # commit. Whatever is true of the repo is what the header says.
    drift_note = (f' &middot; <b class="t-warn">{drifted} changed since verified</b>'
                  if drifted else "")
    ver = (meta_version() or "").lstrip("v")
    design = "v" + ".".join(ver.split(".")[:2]) if ver else E(F.get("design_version", ""))
    _, _out = run(["git", "log", "-1", "--format=%ad", "--date=short", "--",
                   str(MOD / "features.json")])
    stamp = _out.strip().splitlines()[0] if _out.strip() else F.get("updated", "?")
    return f'''  <div class="ph"><h2>Subclass &mdash; {E(design)}</h2>
    <span class="muted">{len(feats)} features &middot; {verified} verified
    ({proven_pct:.0f}%){drift_note} &middot; features.json last changed {E(stamp)}</span></div>
  <div class="scpad">

    <div class="scsec">
      {bar}
      <div class="lgs">{legend}</div>
    </div>

    <div class="sccols">
      <div class="scbox">
        <h3>Not proven yet &mdash; what one launch buys</h3>
        <p class="muted">Everything unverified, grouped by the pass that would
        settle it. The top row is the highest-value single launch.</p>
        <ul class="llist">{launch_rows}</ul>
      </div>
      <div class="scbox warnbox">
        <h3>Publish gate</h3>
        <p class="muted">The README describes these as working. None of them are
        verified. <code>release_check.py</code> cannot see this &mdash; it grades
        publishability, not correctness.</p>
        <ul class="glist">{gate_html}</ul>
      </div>
    </div>

    <div class="scsec">
      <h3>Progression &amp; Warp Dice</h3>
      <p class="muted">{E(F.get("dice", {}).get("note", ""))}
      Technique picks are read from <code>Progressions.lsx</code>, not typed here.
      The seven techniques themselves are listed further down &mdash; they are not
      shown per level because you choose from the pool rather than being granted
      all of them.</p>
      {level_map}
    </div>

    <div class="scsec">
      <h3>Action economy</h3>
      <p class="muted">What a Warpblade can actually spend a turn on, coloured by
      how proven it is.</p>
      <div class="econ">{econ_html}</div>
    </div>

    <div class="scsec">
      <h3>Every feature</h3>
      <p class="muted">Click a row for evidence and what to watch for.</p>
      {"".join(groups)}
    </div>

    <div class="scsec">
      <h3>What the colours mean</h3>
      <div class="lgfull">{legend_full}</div>
    </div>
  </div>'''


def check_features(F) -> int:
    """Cross-check features.json's structural claims against the real mod files.

    Only the checkable half: levels and names against Progressions.lsx and the
    passive list. Status and evidence are on the author's honour by design.
    """
    if not F or "_error" in F:
        print("features.json missing or unparseable")
        return 1

    prog = (PUBLIC / "Progressions/Progressions.lsx").read_text(encoding="utf-8-sig")
    lists = (PUBLIC / "Lists/PassiveLists.lsx").read_text(encoding="utf-8-sig")
    stats = " ".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in (PUBLIC / "Stats").rglob("*.txt"))

    prog_levels = set(int(x) for x in re.findall(r'id="Level"[^>]*value="(\d+)"', prog))
    pool = re.findall(r'Warpblade_Technique_(\w+)', lists)

    bad = 0
    for f in F.get("features", []):
        lv = f.get("level")
        if f.get("kind") in ("technique",) and lv not in prog_levels:
            print(f"  WARN {f['name']}: level {lv} is not a progression row "
                  f"({sorted(prog_levels)})")
            bad += 1
        # a technique should exist in the selectable pool
        if f.get("kind") == "technique":
            key = f["name"].replace(" ", "")
            if key not in pool:
                print(f"  WARN {f['name']}: not found in the technique pool {pool}")
                bad += 1
        # every named feature should appear somewhere in the stats
        stem = f["name"].split(" - ")[0].replace(" ", "")
        if f.get("kind") in ("spell", "passive", "technique") and stem not in stats.replace(" ", ""):
            print(f"  NOTE {f['name']}: no obvious stats entry matching '{stem}'")

    print(f"features.json: {len(F.get('features', []))} features, {bad} structural "
          f"mismatch(es)")
    return 1 if bad else 0


def build() -> str:
    val, rel, tri = validator_state(), release_state(), triage_state()
    feats = features_state()
    pak, cor, mod = pak_state(), corpus_state(), mod_state()
    live, off = parse_queue(LIVE), parse_offline(OFFLINE)
    commits, nxt = git_log(), next_action()

    ORDER = {"attention": 0, "open": 1, "starred": 1, "pinned": 2, "low": 3,
             "note": 4, "done": 5}
    live_done = sum(1 for i in live if i["status"] == "done")
    live_open = [i for i in live if i["status"] not in ("done", "low", "note")]
    off_open = [o for o in off if o["status"] in ("open", "attention", "starred")]
    off_done = sum(1 for o in off if o["status"] == "done")

    def tile(label, value, sub, tone):
        return (f'<div class="tile tone-{tone}"><div class="tl">{E(label)}</div>'
                f'<div class="tv">{E(str(value))}</div><div class="ts">{E(sub)}</div></div>')

    tiles = "".join([
        tile("Validator", f'{val["errors"]}E / {val["warnings"]}W',
             f'{val["accepted"]} accepted', "good" if val["ok"] else "bad"),
        tile("Release", f'{rel["blockers"]} blockers', f'{rel["shoulds"]} should-fix',
             "bad" if rel["blockers"] else ("warn" if rel["shoulds"] else "good")),
        tile("Deployed pak", pak["label"], pak["detail"], "good" if pak["ok"] else "bad"),
        tile("Live passes", f'{live_done}/{len(live)}', f'{len(live_open)} open',
             "good" if not live_open else "warn"),
        tile("Offline queue", len(off_open), f'{off_done} closed',
             "warn" if off_open else "good"),
        tile("Corpus", f'{cor["entries"]:,}', f'{cor["functors"]} functors', "neutral"),
    ])

    def queue_rows(items, label):
        out = []
        for i in sorted(items, key=lambda x: ORDER.get(x["status"], 6)):
            full = i.get("full", "")
            title = label(i)
            more = (f'<div class="detail">{E(full)}</div>'
                    if full and len(full) > len(i["title"]) + 8 else "")
            tab = " tabindex=\"0\"" if more else ""
            prompt = (f"{CLAUDE_TASK_PREAMBLE}\n\n"
                      f"Warpblade work-queue item: {i.get('full') or title}")
            btn = (f'<button class="send" data-prompt="{E(prompt)}" '
                   f'title="Send this to Claude as a task to work on now">'
                   f'Send to Claude</button>')
            out.append(f'<li class="row st-{i["status"]}" data-st="{i["status"]}"{tab}>'
                       f'<span class="pill">{E(i["status"])}</span>'
                       f'<div><div class="rt-head"><span class="rt">{E(title)}</span>{btn}</div>'
                       f'{more}</div></li>')
        return "".join(out)

    subclass_html = subclass_panel(feats)
    live_rows = queue_rows(live, lambda i: i["title"])
    off_rows = queue_rows([o for o in off if o["status"] != "done"],
                          lambda o: f'{o["n"]}. {o["title"]}')

    G2S = {"ERROR": "attention", "WARN": "open", "BLOCKER": "attention",
           "SHOULD": "open", "NOTE": "note"}

    def finding_rows(fs, where_key):
        if not fs:
            return ('<li class="row st-done" data-st="done"><span class="pill">clear</span>'
                    '<div><span class="rt">Nothing to report.</span></div></li>')
        return "".join(
            f'<li class="row st-{G2S.get(f["grade"], "open")}" data-st="{G2S.get(f["grade"], "open")}">'
            f'<span class="pill">{E(f["grade"])}</span>'
            f'<div><span class="rt">{E(f.get(where_key, ""))}</span>'
            f'<div class="detail always">{E(f["message"])}</div></div></li>' for f in fs)

    val_rows = finding_rows(val["findings"], "where")
    rel_rows = finding_rows(rel["findings"], "area")

    if tri["spells"] or tri["statuses"]:
        parts = []
        for s in tri["spells"]:
            done = "COMPLETED" in s["verdict"]
            st = "done" if done else "open"
            parts.append(
                f'<li class="row st-{st}" data-st="{st}">'
                f'<span class="pill">{"cast" if done else "partial"}</span>'
                f'<div><span class="rt">{E(s["id"])}</span>'
                f'<div class="detail always">{E(s["verdict"])} &middot; {E(s["stages"])}</div>'
                f'</div></li>')
        for s in tri["statuses"]:
            parts.append(
                f'<li class="row st-note" data-st="note"><span class="pill">status</span>'
                f'<div><span class="rt">{E(s["id"])}</span>'
                f'<div class="detail always">applied {s["applied"]}x, '
                f'removed {s["removed"]}x</div></div></li>')
        tri_rows = "".join(parts)
    else:
        tri_rows = ('<li class="row st-note" data-st="note"><span class="pill">none</span>'
                    '<div><span class="rt">No Warpblade events in the newest Osiris log.'
                    '</span></div></li>')

    def tool_row(n, t, d, key):
        btn = (f'<button class="run" data-run="{E(key)}" data-name="{E(n)}">Run</button>'
               if key else '')
        return (f'<li class="row st-note" data-st="note"><span class="pill">tool</span><div>'
                f'<div class="rt-head"><span class="rt">{E(n)} &mdash; {E(t)}</span>{btn}</div>'
                f'<div class="detail always">{E(d)}</div></div></li>')
    tool_rows = "".join(tool_row(*x) for x in TOOLS_INFO)

    commit_rows = "".join(
        f'<li class="commit"><code>{E(c["sha"])}</code>'
        f'<span class="c-sub">{E(c["subject"])}</span>'
        f'<span class="c-when">{E(c["when"])}</span></li>' for c in commits)

    m = rel.get("module", {})
    ident = (f'{E(m.get("Name", CFG.name))} v{E(rel.get("version", "?"))} &middot; '
             f'{E(m.get("Author", "?"))} &middot; {mod["entries"]} stats entries &middot; '
             f'{mod["sections"]} reference sections')
    gen = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")

    return f"""<title>Warpblade Build Board</title>
<style>
  /* Brass accent lifted from the mod's own measured icon palette (ICON_BRIEF.md,
     15-50 deg hue band). Neutrals biased cool so the brass reads as chosen.
     Semantic good/warn/bad kept separate from the accent. */
  :root {{
    --bg:#eceef1; --surface:#f7f8fa; --surface-2:#e3e6eb; --line:#cfd4dc;
    --ink:#1b1f26; --ink-2:#4a5361; --ink-3:#79828f;
    --brass:#9a6c22; --brass-dim:#b98c3e;
    --good:#3f7a4d; --warn:#9a6f1c; --bad:#a8402f;
    --info:#31688e; --pinned:#6b4c9a; --idle:#8b95a3;
    --shadow:0 1px 2px rgba(20,26,36,.09), 0 6px 18px rgba(20,26,36,.05);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg:#14171c; --surface:#1b1f26; --surface-2:#232830; --line:#313846;
      --ink:#e7eaef; --ink-2:#aab3c0; --ink-3:#7b8593;
      --brass:#c9973f; --brass-dim:#8d6a2c;
      --good:#6fbf83; --warn:#d6a63f; --bad:#e0765f;
      --info:#6ba7d6; --pinned:#a98cd6; --idle:#79838f;
      --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.28);
    }}
  }}
  :root[data-theme="dark"] {{
    --bg:#14171c; --surface:#1b1f26; --surface-2:#232830; --line:#313846;
    --ink:#e7eaef; --ink-2:#aab3c0; --ink-3:#7b8593;
    --brass:#c9973f; --brass-dim:#8d6a2c;
    --good:#6fbf83; --warn:#d6a63f; --bad:#e0765f;
    --info:#6ba7d6; --pinned:#a98cd6; --idle:#79838f;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.28);
  }}

  * {{ box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--ink); margin:0; padding:30px 22px 60px;
    font:15px/1.55 ui-sans-serif,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; }}
  .wrap {{ max-width:1180px; margin:0 auto; display:flex; flex-direction:column; gap:22px; }}
  code,.tv,.pill,.rt,.detail,.commit code {{
    font-family:ui-monospace,"Cascadia Mono","SF Mono",Menlo,Consolas,monospace; }}

  .eyebrow {{ font-family:ui-monospace,Consolas,monospace; font-size:11px;
    letter-spacing:.14em; text-transform:uppercase; color:var(--brass); }}
  h1 {{ margin:2px 0 0; font-size:29px; letter-spacing:-.015em; text-wrap:balance; }}
  .ident {{ margin:4px 0 0; color:var(--ink-2); font-size:13px; }}

  .strip {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(162px,1fr)); gap:11px; }}
  .tile {{ background:var(--surface); border:1px solid var(--line);
    border-left:3px solid var(--line); border-radius:8px; padding:12px 14px;
    box-shadow:var(--shadow); }}
  .tl {{ font-size:11px; letter-spacing:.09em; text-transform:uppercase; color:var(--ink-3); }}
  .tv {{ font-size:22px; font-variant-numeric:tabular-nums; margin:3px 0 1px; }}
  .ts {{ font-size:12px; color:var(--ink-3); }}
  .tone-good {{ border-left-color:var(--good); }}
  .tone-warn {{ border-left-color:var(--warn); }}
  .tone-bad {{ border-left-color:var(--bad); }}
  .tone-neutral {{ border-left-color:var(--brass-dim); }}

  .next {{ background:var(--surface); border:1px solid var(--line);
    border-left:3px solid var(--brass); border-radius:8px; padding:15px 17px;
    box-shadow:var(--shadow); }}
  .next .tl {{ color:var(--brass); }}
  .next p {{ margin:5px 0 0; max-width:80ch; }}

  nav {{ display:flex; gap:6px; flex-wrap:wrap; }}
  .tab {{ font:inherit; font-size:13px; cursor:pointer; padding:7px 13px; border-radius:7px;
    border:1px solid var(--line); background:var(--surface); color:var(--ink-2); }}
  .tab:hover {{ border-color:var(--brass-dim); color:var(--ink); }}
  .tab[aria-selected="true"] {{ background:var(--brass); border-color:var(--brass);
    color:var(--bg); }}
  .tab:focus-visible, .row:focus-visible, .chip:focus-visible {{
    outline:2px solid var(--brass); outline-offset:2px; }}

  .panel {{ background:var(--surface); border:1px solid var(--line); border-radius:8px;
    box-shadow:var(--shadow); overflow:hidden; }}
  .panel[hidden] {{ display:none; }}
  .ph {{ padding:12px 16px; background:var(--surface-2); border-bottom:1px solid var(--line);
    display:flex; justify-content:space-between; align-items:center; gap:12px;
    flex-wrap:wrap; }}
  .ph h2 {{ margin:0; font-size:13px; letter-spacing:.08em; text-transform:uppercase;
    color:var(--ink-2); }}
  .filters {{ display:flex; gap:5px; }}
  .chip {{ font:inherit; font-size:11px; cursor:pointer; padding:3px 9px; border-radius:999px;
    border:1px solid var(--line); background:var(--bg); color:var(--ink-3); }}
  .chip[aria-pressed="true"] {{ border-color:var(--brass); color:var(--brass); }}

  ul {{ list-style:none; margin:0; padding:0; }}
  .row {{ display:grid; grid-template-columns:86px 1fr; gap:11px; align-items:start;
    padding:11px 16px; border-bottom:1px solid var(--line);
    border-left:3px solid transparent; }}
  .row:last-child {{ border-bottom:none; }}
  .row[tabindex] {{ cursor:pointer; }}
  .row[hidden] {{ display:none; }}
  .rt {{ font-size:12.5px; line-height:1.5; word-break:break-word; }}
  .pill {{ font-size:10px; letter-spacing:.07em; text-transform:uppercase; padding:3px 7px;
    border-radius:999px; text-align:center; border:1px solid var(--line);
    color:var(--ink-3); background:var(--bg); }}
  .detail {{ display:none; margin-top:7px; padding:9px 11px; background:var(--bg);
    border:1px solid var(--line); border-radius:6px; font-size:11.5px; line-height:1.6;
    color:var(--ink-2); white-space:pre-wrap; overflow-x:auto; }}
  .row.open-row .detail, .detail.always {{ display:block; }}
  .st-done {{ border-left-color:var(--good); }}
  .st-done .pill {{ color:var(--good); border-color:var(--good); }}
  .st-done .rt {{ color:var(--ink-3); }}
  .st-attention {{ border-left-color:var(--warn); }}
  .st-attention .pill {{ color:var(--warn); border-color:var(--warn); }}
  .st-open {{ border-left-color:var(--brass-dim); }}
  .st-open .pill {{ color:var(--brass); border-color:var(--brass-dim); }}
  .st-pinned {{ border-left-color:var(--bad); }}
  .st-pinned .pill {{ color:var(--bad); border-color:var(--bad); }}
  .st-low, .st-starred, .st-note {{ border-left-color:var(--ink-3); }}
  .st-note .rt {{ color:var(--ink-2); }}

  .commit {{ display:grid; grid-template-columns:64px 1fr auto; gap:11px; align-items:baseline;
    padding:9px 16px; border-bottom:1px solid var(--line); font-size:12.5px; }}
  .commit:last-child {{ border-bottom:none; }}
  .commit code {{ color:var(--brass); }}
  .c-sub {{ color:var(--ink-2); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .c-when {{ color:var(--ink-3); font-size:11.5px; white-space:nowrap; }}

  .rt-head {{ display:flex; gap:10px; align-items:flex-start; justify-content:space-between; }}
  .run, .send {{ font:inherit; font-size:11px; cursor:pointer; white-space:nowrap;
    padding:3px 10px; border-radius:6px; border:1px solid var(--brass-dim);
    background:var(--bg); color:var(--brass); flex:none; }}
  .run:hover, .send:hover {{ background:var(--brass); border-color:var(--brass);
    color:var(--bg); }}
  .run:focus-visible, .send:focus-visible {{ outline:2px solid var(--brass);
    outline-offset:2px; }}
  .run[disabled] {{ opacity:.55; cursor:progress; }}
  .console {{ background:var(--surface); border:1px solid var(--line);
    border-left:3px solid var(--brass-dim); border-radius:8px; box-shadow:var(--shadow); }}
  .console .ph {{ border-radius:0; }}
  .console pre {{ margin:0; padding:14px 16px; max-height:340px; overflow:auto;
    font-family:ui-monospace,"Cascadia Mono",Consolas,monospace; font-size:11.5px;
    line-height:1.55; color:var(--ink-2); white-space:pre-wrap; word-break:break-word; }}
  .mode {{ font-size:11px; padding:3px 9px; border-radius:999px; border:1px solid var(--line);
    color:var(--ink-3); }}
  .mode.live {{ color:var(--good); border-color:var(--good); }}
  footer {{ color:var(--ink-3); font-size:12px; display:flex; gap:14px; flex-wrap:wrap; }}
  @media (max-width:560px) {{
    .row {{ grid-template-columns:1fr; }}
    .pill {{ justify-self:start; }}
  }}
  .ask {{ margin:18px 0 0; padding:14px 16px; border:1px solid var(--line);
          border-radius:10px; background:var(--card); }}
  .askrow {{ display:flex; gap:8px; margin-top:10px; }}
  .askrow input {{ flex:1; padding:9px 11px; border-radius:8px; font:inherit;
                   border:1px solid var(--line); background:var(--bg); color:var(--fg); }}
  .askhint {{ margin-top:8px; font-size:12px; color:var(--dim); min-height:16px; }}
  .pending {{ font-size:12px; color:var(--dim); }}
  .pending.hot {{ color:var(--warn); font-weight:600; }}
  .pending.ok {{ color:var(--good); }}
  .pending.cold {{ color:var(--bad); font-weight:600; }}
  /* ---- Subclass tab ---- */
  .t-ok {{ --tc:var(--good); }}      .t-info {{ --tc:var(--info); }}
  .t-warn {{ --tc:var(--warn); }}    .t-idle {{ --tc:var(--idle); }}
  .t-bad {{ --tc:var(--bad); }}      .t-pinned {{ --tc:var(--pinned); }}
  .scpad {{ padding:16px; }}
  .scsec {{ margin-bottom:26px; }}
  .scsec h3, .scbox h3 {{ font-size:13px; letter-spacing:.04em; text-transform:uppercase;
    color:var(--ink-2); margin:0 0 8px; }}
  .muted {{ color:var(--ink-3); font-size:12.5px; margin:0 0 10px; }}
  .cbar {{ display:flex; height:12px; border-radius:999px; overflow:hidden;
    border:1px solid var(--line); background:var(--bg); }}
  .seg {{ background:var(--tc); }}
  .lgs {{ display:flex; flex-wrap:wrap; gap:14px; margin-top:10px; font-size:12px;
    color:var(--ink-2); }}
  .lg {{ display:inline-flex; align-items:center; gap:6px; }}
  .lg b {{ color:var(--ink); font-variant-numeric:tabular-nums; }}
  .dot {{ width:9px; height:9px; border-radius:50%; background:var(--tc); display:inline-block; }}
  .sccols {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
    gap:14px; margin-bottom:26px; }}
  .scbox {{ border:1px solid var(--line); border-radius:8px; padding:13px 15px;
    background:var(--bg); }}
  .warnbox {{ border-left:3px solid var(--warn); }}
  .llist, .glist {{ list-style:none; margin:0; padding:0; }}
  .lrow {{ display:flex; align-items:baseline; gap:9px; padding:6px 0;
    border-top:1px solid var(--line); font-size:12.5px; }}
  .lrow:first-child {{ border-top:0; }}
  .pass {{ color:var(--brass); font-weight:600; white-space:nowrap; }}
  .lcount {{ font-variant-numeric:tabular-nums; color:var(--ink); font-weight:700;
    min-width:1.4em; }}
  .lnames {{ color:var(--ink-3); }}
  .grow {{ display:flex; align-items:center; gap:9px; padding:6px 0;
    border-top:1px solid var(--line); font-size:12.5px; }}
  .grow:first-child {{ border-top:0; }}
  .gname {{ flex:1; color:var(--ink); }}
  .gpass {{ color:var(--brass); white-space:nowrap; }}
  .spill {{ font-size:10.5px; letter-spacing:.03em; text-transform:uppercase;
    padding:2px 7px; border-radius:999px; white-space:nowrap;
    color:var(--tc); border:1px solid var(--tc); }}
  .lvlmap {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
    gap:9px; }}
  .lvl {{ border:1px solid var(--line); border-radius:8px; padding:10px 11px;
    background:var(--bg); }}
  .lvn {{ font-size:11px; text-transform:uppercase; letter-spacing:.05em;
    color:var(--ink-3); }}
  .lvd {{ font-size:21px; font-weight:700; color:var(--brass);
    font-variant-numeric:tabular-nums; line-height:1.3; }}
  .die {{ font-size:11px; font-weight:600; color:var(--ink-3); margin-left:5px; }}
  .lvu {{ display:flex; flex-wrap:wrap; gap:4px; margin-top:5px; }}
  .uch {{ font-size:11px; padding:2px 6px; border-radius:5px;
    color:var(--tc); border:1px solid var(--tc); }}
  .lvl.haspick {{ border-color:var(--brass); }}
  .pick {{ margin-top:6px; padding:5px 8px; border-radius:6px; font-size:11.5px;
    color:var(--ink-2); background:var(--surface-2);
    border-left:2px solid var(--brass); }}
  .pick b {{ color:var(--brass); font-size:12.5px; }}
  .pickrun {{ display:block; margin-top:2px; font-size:10.5px; color:var(--ink-3); }}
  .econ {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:11px; }}
  .ecol {{ border:1px solid var(--line); border-radius:8px; padding:10px 11px;
    background:var(--bg); }}
  .ecl {{ font-size:11px; text-transform:uppercase; letter-spacing:.05em;
    color:var(--ink-3); margin-bottom:6px; }}
  .eit {{ display:flex; justify-content:space-between; gap:8px; font-size:12.5px;
    padding:3px 0 3px 9px; border-left:2px solid var(--tc); margin-bottom:3px; }}
  .ecost {{ color:var(--ink-3); font-size:11.5px; white-space:nowrap; }}
  .scgroup {{ margin-bottom:16px; }}
  .scgroup ul {{ list-style:none; margin:0; padding:0;
    border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
  .screw {{ display:flex; gap:11px; align-items:flex-start; padding:11px 14px;
    border-top:1px solid var(--line); border-left:3px solid var(--tc);
    background:var(--bg); cursor:pointer; }}
  .screw:first-child {{ border-top:0; }}
  .screw:focus-visible {{ outline:2px solid var(--brass); outline-offset:-2px; }}
  .scmain {{ flex:1; min-width:0; }}
  .scline {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
  .scname {{ font-weight:600; }}
  .scmeta {{ font-size:11.5px; color:var(--ink-3); margin-top:3px; }}
  .flag {{ font-size:10px; text-transform:uppercase; letter-spacing:.04em;
    padding:1px 6px; border-radius:4px; color:var(--bad); border:1px solid var(--bad); }}
  /* Amber, not red: the feature is not broken, it is unproven in its current form. */
  .flag.drift {{ color:var(--warn,#e0a33e); border-color:var(--warn,#e0a33e); }}
  .scdetail {{ display:none; margin-top:9px; padding:9px 11px; border-radius:6px;
    background:var(--surface-2); font-size:12.5px; color:var(--ink-2); }}
  .screw.open-row .scdetail {{ display:block; }}
  .scdetail p {{ margin:0 0 7px; }}  .scdetail p:last-child {{ margin:0; }}
  .lgfull {{ display:grid; gap:7px; }}
  .lgrow {{ display:flex; gap:10px; align-items:baseline; font-size:12.5px; }}
  .lgd {{ color:var(--ink-3); }}
  @media (prefers-reduced-motion:reduce) {{ * {{ transition:none !important; }} }}
</style>

<div class="wrap">
  <header>
    <div class="eyebrow">Baldur&rsquo;s Gate 3 &middot; Fighter subclass</div>
    <h1>Warpblade Build Board</h1>
    <p class="ident">{ident}</p>
  </header>

  <div class="strip">{tiles}</div>

  <div class="next"><div class="tl">Next action</div><p>{E(nxt)}</p></div>

  <nav role="tablist">
    <button class="tab" role="tab" aria-selected="true"  data-p="p-live">Live queue</button>
    <button class="tab" role="tab" aria-selected="false" data-p="p-sub">Subclass</button>
    <button class="tab" role="tab" aria-selected="false" data-p="p-off">Offline queue</button>
    <button class="tab" role="tab" aria-selected="false" data-p="p-val">Validator</button>
    <button class="tab" role="tab" aria-selected="false" data-p="p-rel">Release</button>
    <button class="tab" role="tab" aria-selected="false" data-p="p-tri">Last play session</button>
    <button class="tab" role="tab" aria-selected="false" data-p="p-tools">Tools</button>
    <button class="tab" role="tab" aria-selected="false" data-p="p-git">Commits</button>
  </nav>

  <section class="panel" id="p-live">
    <div class="ph"><h2>Live queue &mdash; needs the game running</h2>
      <div class="filters">
        <button class="chip" aria-pressed="true"  data-f="all">all</button>
        <button class="chip" aria-pressed="false" data-f="todo">open only</button>
      </div></div>
    <ul>{live_rows}</ul>
  </section>

  <section class="panel" id="p-sub" hidden>
{subclass_html}
  </section>

  <section class="panel" id="p-off" hidden>
    <div class="ph"><h2>Offline queue &mdash; {len(off_open)} open, {off_done} closed</h2>
      <div class="filters">
        <button class="chip" aria-pressed="true"  data-f="all">all</button>
        <button class="chip" aria-pressed="false" data-f="todo">open only</button>
      </div></div>
    <ul>{off_rows}</ul>
  </section>

  <section class="panel" id="p-val" hidden>
    <div class="ph"><h2>validate.py &mdash; {val["errors"]} errors, {val["warnings"]} warnings,
      {val["accepted"]} accepted</h2></div>
    <ul>{val_rows}</ul>
  </section>

  <section class="panel" id="p-rel" hidden>
    <div class="ph"><h2>release_check.py &mdash; {rel["blockers"]} blockers,
      {rel["shoulds"]} should-fix</h2></div>
    <ul>{rel_rows}</ul>
  </section>

  <section class="panel" id="p-tri" hidden>
    <div class="ph"><h2>triage_log.py &mdash; {E(tri.get("log") or "no log found")}</h2>
      <span class="ts">{E(tri.get("when", ""))}</span></div>
    <ul>{tri_rows}</ul>
  </section>

  <section class="panel" id="p-tools" hidden>
    <div class="ph"><h2>Toolchain</h2></div>
    <ul>{tool_rows}</ul>
  </section>

  <section class="panel" id="p-git" hidden>
    <div class="ph"><h2>Recent commits</h2></div>
    <ul>{commit_rows}</ul>
  </section>

  <section class="console" id="console" hidden>
    <div class="ph"><h2 id="con-title">Output</h2>
      <button class="chip" id="con-close">close</button></div>
    <pre id="con-body"></pre>
  </section>

  <section class="ask" id="ask">
    <div class="ph"><h2>Send to Claude</h2>
      <span class="pending" id="ask-pending"></span></div>
    <div class="askrow">
      <input id="ask-text" type="text" autocomplete="off"
             placeholder="Type anything for Claude, or hit Send to Claude on any row above">
      <button class="run" id="ask-send">Send</button>
    </div>
    <div class="askhint" id="ask-hint"></div>
  </section>

  <footer>
    <span>Generated {E(gen)}</span>
    <span>py tools/dashboard.py --serve</span>
    <span id="mode-note"></span>
  </footer>
  <!--SERVE-->
</div>

<script>
(function () {{
  var tabs = [].slice.call(document.querySelectorAll('.tab'));
  tabs.forEach(function (t) {{
    t.addEventListener('click', function () {{
      tabs.forEach(function (o) {{
        o.setAttribute('aria-selected', String(o === t));
        document.getElementById(o.dataset.p).hidden = (o !== t);
      }});
    }});
  }});

  function toggle(row) {{ row.classList.toggle('open-row'); }}

  document.addEventListener('click', function (e) {{
    // A click on a button inside a row is for the button, not the row. This
    // listener is registered before the run/send handlers, so their
    // stopPropagation() cannot save us - bail here instead.
    if (e.target.closest && e.target.closest('button')) return;
    var row = e.target.closest ? e.target.closest('.row[tabindex], .screw[tabindex]') : null;
    if (row) toggle(row);
  }});
  document.addEventListener('keydown', function (e) {{
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var row = e.target.closest ? e.target.closest('.row[tabindex], .screw[tabindex]') : null;
    if (row) {{ e.preventDefault(); toggle(row); }}
  }});

  // --- run buttons -------------------------------------------------------
  // Live only when served by dashboard.py --serve. A published artifact is
  // sandboxed and cannot execute anything, so there we copy the command instead.
  var SERVE = !!window.__SERVE__;
  var con = document.getElementById('console');
  var conTitle = document.getElementById('con-title');
  var conBody = document.getElementById('con-body');
  document.getElementById('con-close').addEventListener('click', function () {{
    con.hidden = true;
  }});
  document.getElementById('mode-note').textContent = SERVE
    ? 'Live mode - buttons run the real tools.'
    : 'Read-only view - buttons copy the command to your clipboard.';
  if (SERVE) {{
    var mn = document.getElementById('mode-note');
    mn.className = 'mode live';
  }}

  function show(title, text) {{
    conTitle.textContent = title;
    conBody.textContent = text;
    con.hidden = false;
    con.scrollIntoView({{block: 'nearest'}});
  }}

  function copy(text, okMsg) {{
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).then(function () {{
        show('Copied to clipboard', okMsg + '\\n\\n' + text);
      }}, function () {{ show('Copy failed', text); }});
    }} else {{
      show('Copy this', text);
    }}
  }}

  document.addEventListener('click', function (e) {{
    var r = e.target.closest ? e.target.closest('.run') : null;
    if (r) {{
      e.stopPropagation();
      var key = r.dataset.run, name = r.dataset.name;
      if (!SERVE) {{
        copy('py tools/dashboard.py --serve',
             'This page is read-only. Run the board locally to get live buttons:');
        return;
      }}
      r.disabled = true;
      var was = r.textContent;
      r.textContent = 'Running';
      show(name, 'running...');
      fetch('/run/' + key, {{method: 'POST'}})
        .then(function (res) {{ return res.json(); }})
        .then(function (d) {{
          show(name + (d.ok ? '  [ok]' : '  [exit ' + d.code + ']'),
               d.output || '(no output)');
        }})
        .catch(function (err) {{ show(name + '  [failed]', String(err)); }})
        .then(function () {{ r.disabled = false; r.textContent = was; }});
      return;
    }}
    var sbtn = e.target.closest ? e.target.closest('.send') : null;
    if (sbtn) {{
      e.stopPropagation();
      if (!SERVE) {{ copy(sbtn.dataset.prompt, 'Paste this to Claude:'); return; }}
      sendToClaude(sbtn.dataset.prompt, 'row', sbtn);
    }}
  }});

  // --- send to Claude ----------------------------------------------------
  // Posts to /ask, which appends to dist/_claude_queue.jsonl. Claude reads
  // that file and acts on it. Nothing here executes anything by itself.
  var askHint = document.getElementById('ask-hint');
  var askPending = document.getElementById('ask-pending');
  var askText = document.getElementById('ask-text');

  function paintPending(n, listening, expiresIn) {{
    if (!askPending) return;
    var queued = n ? (n + ' waiting for Claude') : 'nothing waiting';
    if (listening) {{
      var mins = Math.max(1, Math.round((expiresIn || 0) / 60));
      askPending.textContent = queued + ' · Claude is listening (~' + mins + ' min left)';
      askPending.className = 'pending' + (n ? ' hot' : ' ok');
    }} else {{
      // Do not imply anything is coming. A queued item with no listener sits
      // there until the user says something to Claude.
      askPending.textContent = n
        ? (queued + ' · NOT listening - say anything to Claude and it will pick these up')
        : (queued + ' · not listening');
      askPending.className = 'pending' + (n ? ' cold' : '');
    }}
  }}

  function sendToClaude(text, ctx, btn) {{
    if (!text) return;
    var was = btn ? btn.textContent : '';
    if (btn) {{ btn.disabled = true; btn.textContent = 'Sending'; }}
    fetch('/ask', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{text: text, ctx: ctx || ''}})
    }})
      .then(function (r) {{ return r.json(); }})
      .then(function (d) {{
        if (d.ok) {{
          paintPending(d.pending, d.listening, d.expires_in);
          if (askHint) askHint.textContent = 'Queued #' + d.id +
            '. Claude picks it up when it next checks the queue.';
          if (btn) btn.textContent = 'Sent';
          setTimeout(function () {{
            if (btn) {{ btn.textContent = was; btn.disabled = false; }}
          }}, 1600);
          return;
        }}
        if (askHint) askHint.textContent = 'Not queued: ' + (d.error || 'unknown error');
        if (btn) {{ btn.textContent = was; btn.disabled = false; }}
      }})
      .catch(function (err) {{
        if (askHint) askHint.textContent = 'Not queued: ' + String(err);
        if (btn) {{ btn.textContent = was; btn.disabled = false; }}
      }});
  }}

  var askBtn = document.getElementById('ask-send');
  if (askBtn) {{
    askBtn.addEventListener('click', function () {{
      if (!SERVE) {{
        copy('py tools/dashboard.py --serve',
             'This page is read-only. Run the board locally to send to Claude:');
        return;
      }}
      var t = askText ? askText.value.trim() : '';
      if (!t) {{ if (askHint) askHint.textContent = 'Type something first.'; return; }}
      sendToClaude(t, 'composer', askBtn);
      if (askText) askText.value = '';
    }});
  }}
  if (askText) {{
    askText.addEventListener('keydown', function (e) {{
      if (e.key === 'Enter') {{ e.preventDefault(); askBtn.click(); }}
    }});
  }}

  if (SERVE) {{
    var refresh = function () {{
      fetch('/queue').then(function (r) {{ return r.json(); }})
        .then(function (d) {{ paintPending(d.pending, d.listening, d.expires_in); }})
        .catch(function () {{}});
    }};
    refresh();
    setInterval(refresh, 4000);
  }} else {{
    paintPending(0, false, 0);
    if (askHint) askHint.textContent =
      'Read-only view. Run the board locally to send anything to Claude.';
  }}

  [].slice.call(document.querySelectorAll('.filters')).forEach(function (bar) {{
    var panel = bar.closest('.panel');
    bar.addEventListener('click', function (e) {{
      var b = e.target.closest('.chip');
      if (!b) return;
      [].slice.call(bar.querySelectorAll('.chip')).forEach(function (o) {{
        o.setAttribute('aria-pressed', String(o === b));
      }});
      var todo = b.dataset.f === 'todo';
      [].slice.call(panel.querySelectorAll('.row')).forEach(function (r) {{
        var s = r.dataset.st;
        r.hidden = todo && (s === 'done' || s === 'note' || s === 'low');
      }});
    }});
  }});
}})();
</script>
"""


# ------------------------------------------------------------------ serve ---
# A published Artifact is sandboxed and can never run a local script - the
# available runtime capabilities (artifact / downloads / mcp) cover shared state,
# file downloads and cloud connectors, not command execution. So the "click to run"
# buttons are only live in this local server mode; in the artifact they degrade to
# copy-the-command.
#
# Two deliberate constraints, because this is a web page that runs commands:
#   * bound to 127.0.0.1 only - never reachable off this machine;
#   * a fixed ALLOWLIST of argv arrays - the page names a key, never a command,
#     so there is no path from page input to a shell.
# Requests the user sends from the board land here as one JSON object per line.
# Claude reads this file directly - it is plain append-only text on a real disk
# path (C:\Modding is not MSIX-virtualised), which is the only handoff that
# survives a page reload, a server restart, and the container boundary.
CLAUDE_QUEUE = MOD / "dist/_claude_queue.jsonl"
CLAUDE_SEEN = MOD / "dist/_claude_queue.seen"


def queue_ask(text: str, ctx: str = "") -> dict:
    """Append one request from the board. Returns the stored record."""
    import json as _json
    import time as _time
    CLAUDE_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    prev = 0
    if CLAUDE_QUEUE.is_file():
        for ln in CLAUDE_QUEUE.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    prev = max(prev, int(_json.loads(ln).get("id", 0)))
                except (ValueError, TypeError):
                    pass
    rec = {"id": prev + 1, "ts": _time.strftime("%Y-%m-%d %H:%M:%S"),
           "text": text[:4000], "ctx": ctx[:200]}
    with io.open(CLAUDE_QUEUE, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(_json.dumps(rec) + "\n")
    return rec


# Claude's queue listener is a background shell command, and those are capped at
# 10 minutes here. So "1 waiting for Claude" can be true while nothing is actually
# listening - which is exactly how a real click got silently dropped. The listener
# writes its expiry epoch to this file when it arms; the page reads it and says so.
CLAUDE_LISTEN = MOD / "dist/_claude_listening"


def listening_state() -> dict:
    """Is a Claude listener currently armed, and for how much longer?"""
    import time as _time
    if not CLAUDE_LISTEN.is_file():
        return {"listening": False, "expires_in": 0}
    try:
        until = float(CLAUDE_LISTEN.read_text(encoding="utf-8").strip() or 0)
    except (ValueError, OSError):
        return {"listening": False, "expires_in": 0}
    left = int(until - _time.time())
    return {"listening": left > 0, "expires_in": max(0, left)}


def queue_state() -> dict:
    """Everything queued, plus how far Claude has acknowledged."""
    import json as _json
    items = []
    if CLAUDE_QUEUE.is_file():
        for ln in CLAUDE_QUEUE.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    items.append(_json.loads(ln))
                except ValueError:
                    pass
    seen = 0
    if CLAUDE_SEEN.is_file():
        try:
            seen = int(CLAUDE_SEEN.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            seen = 0
    return {"items": items, "seen": seen,
            "pending": sum(1 for i in items if i.get("id", 0) > seen),
            **listening_state()}


ALLOWLIST: dict[str, list[str]] = {
    "validate":     [sys.executable, str(TOOLS / "validate.py")],
    "release":      [sys.executable, str(TOOLS / "release_check.py")],
    "triage":       [sys.executable, str(TOOLS / "triage_log.py")],
    "corpus":       [sys.executable, str(TOOLS / "corpus_index.py")],
    "corpus_report": [sys.executable, str(TOOLS / "corpus_report.py")],
    "inherit":      [sys.executable, str(TOOLS / "inherit_audit.py"), "--all"],
    "loca":         [sys.executable, str(TOOLS / "loca_lint.py")],
    "balance":      [sys.executable, str(TOOLS / "balance_sim.py")],
    "icons":        [sys.executable, str(TOOLS / "icon_atlas.py"), "--names"],
    "conflict":     [sys.executable, str(TOOLS / "conflict_probe.py")],
    "emit_info":    [sys.executable, str(TOOLS / "release_check.py"), "--emit-info"],
    "build":        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", str(MOD / "build.ps1")],
    "build_check":  ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", str(MOD / "build.ps1"), "-SkipPack"],
}


def serve(port: int, open_browser: bool) -> int:
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):                                   # noqa: N802
            if self.path.split("?")[0] in ("/", "/index.html"):
                page = build()
                bad = check_js(page)
                if bad:
                    body = "<pre>generated JS does not parse:\n\n" + html.escape(bad) + "</pre>"
                    self._send(500, body.encode(), "text/html; charset=utf-8")
                    return
                page = page.replace("<!--SERVE-->",
                                    "<script>window.__SERVE__=1;</script>")
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path.split("?")[0] == "/queue":
                self._send(200, _json.dumps(queue_state()).encode())
            else:
                self._send(404, b'{"error":"not found"}')

        def _body(self) -> dict:
            try:
                n = int(self.headers.get("Content-Length") or 0)
                return _json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, TypeError):
                return {}

        def do_POST(self):                                  # noqa: N802
            # /ask queues work for Claude; /ack is Claude marking it handled.
            if self.path == "/ask":
                b = self._body()
                text = (b.get("text") or "").strip()
                if not text:
                    self._send(400, _json.dumps(
                        {"ok": False, "error": "empty request"}).encode())
                    return
                rec = queue_ask(text, b.get("ctx") or "")
                st = queue_state()
                self._send(200, _json.dumps(
                    {"ok": True, "id": rec["id"], "pending": st["pending"]}).encode())
                return
            if self.path == "/ack":
                b = self._body()
                try:
                    CLAUDE_SEEN.parent.mkdir(parents=True, exist_ok=True)
                    CLAUDE_SEEN.write_text(str(int(b.get("id", 0))), encoding="utf-8")
                except (ValueError, TypeError, OSError) as exc:
                    self._send(400, _json.dumps(
                        {"ok": False, "error": str(exc)}).encode())
                    return
                self._send(200, _json.dumps(
                    {"ok": True, "pending": queue_state()["pending"]}).encode())
                return

            m = re.match(r"^/run/([a-z_]+)$", self.path)
            if not m or m.group(1) not in ALLOWLIST:
                self._send(400, _json.dumps(
                    {"ok": False, "output": "unknown or disallowed action"}).encode())
                return
            key = m.group(1)
            code, out = run(ALLOWLIST[key], cwd=str(TOOLS), timeout=600)
            self._send(200, _json.dumps(
                {"ok": code == 0, "code": code, "output": out[-20000:]}).encode())

        def log_message(self, *a):                          # quiet
            pass

    # Windows + SO_REUSEADDR (which HTTPServer sets by default) lets a SECOND
    # process bind a port that is already being served, and then it is undefined
    # which one answers. That silently served stale code for a whole debugging
    # round: the file on disk had the new routes, the process answering did not.
    # Refuse to start rather than reproduce that.
    import socket as _socket
    probe = _socket.socket()
    probe.settimeout(0.4)
    try:
        probe.connect(("127.0.0.1", port))
        print(f"port {port} is ALREADY serving - refusing to start a second board.")
        print("  Stop the running one first, or use --port to pick another.")
        print("  PowerShell:  Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |")
        print("                 Where-Object {{ $_.CommandLine -like '*dashboard.py*' }} |")
        print("                 ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
        return 2
    except OSError:
        pass                      # nothing there: good, the port is ours to take
    finally:
        probe.close()

    ThreadingHTTPServer.allow_reuse_address = 0
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Warpblade build board serving at {url}")
    if not open_browser:
        print("  not opening a browser (pass --open-browser if you want one)")
    print("  buttons are LIVE in this mode; Ctrl-C to stop")
    print(f"  runnable actions: {', '.join(sorted(ALLOWLIST))}")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def check_js(page: str) -> str | None:
    """Syntax-check the emitted <script> with node, if node is available.

    Learned the hard way: an escaping slip put a real newline inside a JS string
    literal, which killed the whole IIFE - so even the tabs stopped responding.
    A broken page looks identical to a working one until you click something, so
    the generator now checks its own output.
    """
    m = re.search(r"<script>(.*?)</script>", page, re.S)
    if not m:
        return None
    import shutil, tempfile
    if not shutil.which("node"):
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(m.group(1))
        tmp = fh.name
    code, out = run(["node", "--check", tmp])
    try:
        Path(tmp).unlink()
    except OSError:
        pass
    return None if code == 0 else out.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="open the written file")
    ap.add_argument("--check-features", action="store_true",
                    help="cross-check features.json against the real mod files")
    # OFF by default as of 2026-08-19, per user instruction: --serve used to pop the
    # user's default browser every time it started. The board is normally driven
    # in Claude's own browser pane, so opening a second copy in the user's browser
    # is unwanted noise. Pass --open-browser to get the old behaviour back.
    ap.add_argument("--open-browser", action="store_true",
                    help="also open the board in your default browser (off by default)")
    ap.add_argument("--serve", action="store_true",
                    help="run a local server so the buttons actually execute the tools")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()

    # THIS FILE NO LONGER RENDERS THE BOARD. It is now the BG3 domain LIBRARY behind
    # tools/board_bg3.py: features_state(), subclass_panel(), the collectors and the
    # drift check all still live here and are all still used. What moved out is the page
    # itself, to vahlok-core/core/scripts/board.py, which Brendan also uses.
    #
    # The guard exists because THIS is how the fork happened the first time: two copies
    # of a page renderer, one of them convenient, and every improvement landing only in
    # the convenient one. Brendan's copy silently missed a classifier fix that was
    # counting 11 of his reference notes as outstanding work. Rendering from here again
    # would reopen exactly that gap, so it refuses rather than quietly diverging.
    if a.serve or a.open or a.open_browser:
        print("dashboard.py no longer renders the board - it is the BG3 domain library "
              "behind tools/board_bg3.py.\n"
              "Use the shared engine, which has the same panels plus the exceptions strip:\n"
              "  py C:\\Modlists\\vahlok-core\\core\\scripts\\board.py "
              "--config C:\\Modlists\\vahlok-core\\core\\scripts\\board.bg3-warpblade.json "
              "--serve\n"
              "(--check-features still works here.)", file=sys.stderr)
        return 2

    if a.check_features:
        return check_features(features_state())
    if a.serve:
        return serve(a.port, a.open_browser)

    page = build()
    err = check_js(page)
    if err:
        print("REFUSING TO WRITE - the generated JavaScript does not parse:\n" + err,
              file=sys.stderr)
        return 2
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT}")
    if a.open:
        import webbrowser
        webbrowser.open(OUT.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
