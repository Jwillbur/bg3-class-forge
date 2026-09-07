"""
Release-readiness audit for the Warpblade mod.

validate.py asks "is this correct?". This asks "is this publishable?" - the metadata,
packaging and text a mod browser and a human reader see, none of which affects whether
the mod works in game and all of which is invisible until someone tries to install it.

    py release_check.py              # audit, exit 1 on any BLOCKER
    py release_check.py --emit-info  # write dist/info.json from meta.lsx + the built pak
    py release_check.py --json       # machine-readable, for the dashboard

Findings are graded:
    BLOCKER  cannot ship until fixed
    SHOULD   ship without it and someone will complain
    NOTE     worth knowing, not worth blocking on
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_index import CFG  # noqa: E402  (one locator, one config load)

# --- mod config -----------------------------------------------------------
# Anchored on the MOD BEING AUDITED (the cwd), not on this file. Until
# 2026-09-06 every path below was the literal string CFG.name, so running
# this from another mod silently audited Warpblade instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modconfig  # noqa: E402
CFG = modconfig.load(Path.cwd())

MOD = CFG.root
META = CFG.meta
LOCA = CFG.loca
PAK = CFG.pak
INFO = MOD / "dist/info.json"
GUI = CFG.gui

findings: list[tuple[str, str, str]] = []       # (grade, area, message)


def blocker(area, msg): findings.append(("BLOCKER", area, msg))
def should(area, msg):  findings.append(("SHOULD", area, msg))
def note(area, msg):    findings.append(("NOTE", area, msg))


def module_info() -> dict:
    """Fields of the mod's own ModuleInfo node (not its dependency entries)."""
    if not META.exists():
        return {}
    s = io.open(META, encoding="utf-8").read()
    m = re.search(r'<node id="ModuleInfo">(.*?)</node>', s, re.S)
    if not m:
        return {}
    body = m.group(1)
    out = {}
    for k in ("Name", "UUID", "Version64", "Author", "Description", "Tags",
              "Folder", "MD5", "Type"):
        mm = re.search(rf'id="{k}"[^/]*value="([^"]*)"', body)
        out[k] = mm.group(1) if mm else ""
    return out


def design_version() -> str | None:
    """What the DOCS call this build - read, not remembered.

    This was a hardcoded constant with a comment telling whoever came next to bump it
    alongside meta.lsx. Nobody did, so it sat at 1.7 while 1.8.0.0 shipped and the check
    reported the mod out of step with a number that was itself the stale half. A value a
    human has to remember to update is not a source of truth, it is a second thing to
    forget - and this file's whole job is catching that class of mistake.

    The CHANGELOG's newest version heading is the real answer: it is written by the same
    act that ships a version, so it cannot drift without someone noticing.
    """
    ch = MOD / "CHANGELOG.md"
    if not ch.is_file():
        return None
    m = re.search(r"^##\s*v(\d+)\.(\d+)", ch.read_text(encoding="utf-8"), re.M)
    return f"{m.group(1)}.{m.group(2)}" if m else None


def decode_version(v: str) -> str:
    if not v.isdigit():
        return "?"
    n = int(v)
    return f"{n >> 55}.{(n >> 47) & 0xFF}.{(n >> 31) & 0xFFFF}.{n & 0x7FFFFFFF}"


def declared_dependencies() -> list[str]:
    if not META.exists():
        return []
    s = io.open(META, encoding="utf-8").read()
    dep = re.search(r'<node id="Dependencies">(.*?)</node>\s*</children>', s, re.S)
    block = dep.group(1) if dep else s
    names = re.findall(r'id="Name"[^/]*value="([^"]*)"', block)
    return [n for n in names if n != CFG.name]


def audit() -> dict:
    mi = module_info()
    if not mi:
        blocker("meta.lsx", "no ModuleInfo node found - meta.lsx is missing or malformed")
        return {"module": {}, "findings": findings}

    # --- identity -----------------------------------------------------------
    if not mi["Author"] or "AUTHOR_NAME_HERE" in mi["Author"]:
        blocker("meta.lsx", "Author is still the placeholder 'AUTHOR_NAME_HERE'. "
                            "Mod managers show this verbatim on the mod card.")
    if not mi["Description"].strip():
        should("meta.lsx", "Description is empty - this is the one-liner mod browsers show.")
    if not mi["Tags"].strip():
        should("meta.lsx", "Tags is empty. Browsers filter on it; 'Class'/'Subclass' at minimum.")
    if not mi["UUID"]:
        blocker("meta.lsx", "UUID is empty - the mod cannot be referenced or depended on.")

    # The design version lives in the docs; the shipped version lives here. They are
    # unrelated fields, so this only earns a note when they actually disagree - an
    # unconditional one just trains you to ignore it.
    ver = decode_version(mi["Version64"])
    dv = design_version()
    if dv is None:
        note("CHANGELOG.md", "no '## vX.Y' heading found, so the shipped version cannot "
                             "be checked against what the docs call this build.")
    elif not ver.startswith(dv + "."):
        note("meta.lsx", f"Version64 {mi['Version64']} decodes to v{ver}, but the newest "
                         f"CHANGELOG heading calls this v{dv}. One of the two was not "
                         f"updated - pick one story before release.")

    # --- packaging ----------------------------------------------------------
    if not PAK.exists():
        blocker("packaging", "no built pak in dist/ - run build.ps1")
    else:
        if not INFO.exists():
            should("packaging", "no dist/info.json. Every shipped mod examined (Nexus 3939, "
                                "15060, 137) ships one; BG3MM and mod.io read it. "
                                "Generate with --emit-info.")
        else:
            try:
                data = json.loads(io.open(INFO, encoding="utf-8").read())
                m0 = (data.get("Mods") or [{}])[0]
                if m0.get("UUID") != mi["UUID"]:
                    blocker("packaging", f"info.json UUID {m0.get('UUID')} does not match "
                                         f"meta.lsx {mi['UUID']}")
                if m0.get("Version") != mi["Version64"]:
                    should("packaging", "info.json Version does not match meta.lsx Version64")
                if data.get("MD5") and PAK.exists():
                    live = hashlib.md5(PAK.read_bytes()).hexdigest()
                    if live != data["MD5"]:
                        should("packaging", "info.json MD5 is stale - the pak was rebuilt "
                                            "after it was written. Re-run --emit-info.")
            except (ValueError, KeyError) as exc:
                blocker("packaging", f"dist/info.json is not readable: {exc}")

    # --- dependencies -------------------------------------------------------
    # ⛔ Until 2026-09-06 this block asserted that EVERY mod needs Compatibility
    #   Framework, because the first mod it was written against did. Oath of Avernus
    #   does not: its ClassDescription registers the subclass by ParentGuid - the same
    #   shape all four shipped Paladin subclasses use - and it overrides no vanilla
    #   file, so there is nothing for CF to reconcile. A blocker that fires on a
    #   correct mod trains you to ignore blockers.
    #   The requirement is now per-mod, declared in forge.json:
    #       "requires": ["Compatibility Framework", "CommunityLibrary"]
    #   Absent or empty means the mod needs no framework, and the check becomes
    #   "does the meta declare what forge.json says you need".
    deps = declared_dependencies()
    required = CFG.data.get("requires") or []
    if isinstance(required, str):
        required = [required]
    for want in required:
        key = want.replace(" ", "").lower()
        if not any(key in d.replace(" ", "").lower() for d in deps):
            blocker("dependencies", f"forge.json requires {want!r}, "
                                    f"and meta.lsx does not declare it. Found: {deps or 'none'}")
    if required and any("Compatibility" in d for d in required) \
            and not any("CommunityLibrary" in d.replace(" ", "") for d in required):
        note("dependencies", "CommunityLibrary is not declared directly. It is a hard "
                             "requirement of CF, so it resolves transitively, but users "
                             "installing manually see only what you declare.")
    if not required and deps:
        note("dependencies", f"meta.lsx declares {deps}, but forge.json lists no "
                             f"'requires'. One of the two is out of date.")

    # --- player-facing text -------------------------------------------------
    if not LOCA.exists():
        blocker("localisation", "no English localisation file")
    else:
        x = io.open(LOCA, encoding="utf-8").read()
        ents = re.findall(r'<content contentuid="(h[0-9a-fg]+)"[^>]*>(.*?)</content>', x, re.S)
        empty = [u for u, t in ents if not t.strip()]
        ph = [(u, t) for u, t in ents if re.search(r"TODO|PLACEHOLDER|TBD|XXX|Lorem", t, re.I)]
        if empty:
            blocker("localisation", f"{len(empty)} handle(s) declared with empty text")
        if ph:
            blocker("localisation", f"{len(ph)} handle(s) still contain placeholder text")
        note("localisation", f"{len(ents)} handles, English only. Other languages fall back "
                             f"to English in game - not a blocker, just untranslated.")

    # --- icons --------------------------------------------------------------
    # Scan every shipped tree, not just Mods/*/GUI. The recoloured spell atlas
    # lives under Public/Warpblade/Assets/Textures/Icons, so the original glob
    # reported "13 DDS shipped" on a build that shipped 14 - and, worse, the
    # BC7_UNORM_SRGB check never looked at it.
    icon_roots = [GUI, MOD / "Public"]
    if GUI.is_dir():
        dds = sorted({p for root in icon_roots if root.is_dir()
                      for p in root.rglob("*.DDS")})
        srgb = []
        for p in dds:
            b = p.read_bytes()[:148]
            if len(b) >= 132 and b[84:88] == b"DX10":
                fmt = int.from_bytes(b[128:132], "little")
                if fmt == 99:                     # BC7_UNORM_SRGB
                    srgb.append(p.name)
        if srgb:
            blocker("icons", f"{len(srgb)} DDS still encoded BC7_UNORM_SRGB "
                             f"({', '.join(sorted(set(srgb))[:3])}) - vanilla is always "
                             f"BC7_UNORM; this is the washed-out bug (ref S21.2b).")
        note("icons", f"{len(dds)} DDS shipped.")
    else:
        should("icons", "no GUI folder - the mod will show blank icons")

    # --- documentation ------------------------------------------------------
    readme = MOD / "README.md"
    if readme.exists():
        txt = io.open(readme, encoding="utf-8").read()
        if len(txt) > 8000 and "Engine feasibility review" in txt:
            should("docs", "README.md is the ~30 KB development/engine document, not a "
                           "player-facing description. Write a short user README before "
                           "publishing, or the mod page will read as internal notes.")
        # AI DISCLOSURE. A BLOCKER on purpose, and the reason is not legal - it is
        # that the BG3 community is currently split on AI-assisted mods, and being
        # found out later is far worse than being upfront now. Anyone can rewrite a
        # README; nobody should be able to quietly drop this while doing it.
        #
        # It also catches a specific mistake already made once: an earlier Credits
        # line read "every file in here was written and packed by hand", which was
        # not true of this mod and would have read as a cover-up rather than a slip.
        low = txt.lower()
        if not (("ai" in low.split() or "ai-" in low or " ai " in low)
                and ("made with ai" in low or "ai was used" in low
                     or "ai-generated" in low or "ai-assisted" in low)):
            blocker("docs", "README.md has no AI-use disclosure. This mod was built with AI "
                            "assistance and the page must say so plainly, near the top. "
                            "Publishing without it is how a project gets its reputation "
                            "wrecked after the fact rather than judged on its merits.")
        for claim in ("written and packed by hand", "entirely by hand", "no ai", "human-made",
                      "hand-written throughout"):
            if claim in low:
                blocker("docs", f"README.md claims \"{claim}\" - check that against the AI "
                                f"disclosure. One of the two statements is false.")

    # CHANGELOG vs the shipped version. Caught for real on publish day: every
    # heading still said "[Unreleased]" and the newest one was v1.5.4, while the
    # pak in dist/ was v1.6.7.0 - twelve versions of work, including the whole
    # VFX pass, missing from the tab people read second after the description.
    # Nothing else in this file looks at the changelog's CONTENT, so nothing
    # else could have noticed.
    chlog = MOD / "CHANGELOG.md"
    if not chlog.exists():
        note("docs", "no CHANGELOG.md. Optional, but every update after the first will "
                     "want one.")
    else:
        ctxt = io.open(chlog, encoding="utf-8-sig").read()
        heads = re.findall(r"^##\s+(.*)$", ctxt, re.M)
        if not heads:
            should("docs", "CHANGELOG.md has no '## <version>' headings - nothing to "
                           "publish as release notes.")
        else:
            top = heads[0]
            if "unreleased" in top.lower():
                blocker("docs", f"CHANGELOG.md's newest heading is still \"{top}\". You are "
                                f"shipping it; label it with the version that ships.")
            found = re.search(r"v?(\d+\.\d+(?:\.\d+){0,2})", top)
            if found and not ver.startswith(found.group(1).rstrip(".0") or found.group(1)):
                if found.group(1).split(".")[:2] != ver.split(".")[:2]:
                    blocker("docs", f"CHANGELOG.md's newest entry is v{found.group(1)} but the "
                                    f"pak ships v{ver}. The release notes describe a different "
                                    f"build than the download.")

    return {"module": mi, "version": ver, "deps": deps, "findings": findings}


def emit_info() -> int:
    """Write dist/info.json from meta.lsx and the built pak."""
    mi = module_info()
    if not mi:
        print("cannot read meta.lsx", file=sys.stderr)
        return 1
    if not PAK.exists():
        print("no built pak - run build.ps1 first", file=sys.stderr)
        return 1
    md5 = hashlib.md5(PAK.read_bytes()).hexdigest()
    data = {
        "Mods": [{
            "Author": mi["Author"],
            "Name": mi["Name"],
            "Folder": mi["Folder"],
            "Version": mi["Version64"],
            "Description": mi["Description"],
            "UUID": mi["UUID"],
            "Created": datetime.now(timezone.utc).astimezone().isoformat(),
            "Dependencies": declared_dependencies(),
            "Group": "",
        }],
        "MD5": md5,
    }
    INFO.parent.mkdir(parents=True, exist_ok=True)
    io.open(INFO, "w", encoding="utf-8").write(json.dumps(data, indent=1))
    print(f"wrote {INFO}")
    print(f"  UUID {mi['UUID']}  v{decode_version(mi['Version64'])}  md5 {md5}")
    if "AUTHOR_NAME_HERE" in mi["Author"]:
        print("  WARNING: Author is still the placeholder - fix meta.lsx and re-run.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-info", action="store_true", help="write dist/info.json")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    if a.emit_info:
        return emit_info()

    res = audit()
    if a.json:
        print(json.dumps({"version": res.get("version"),
                          "module": res.get("module"),
                          "findings": [{"grade": g, "area": ar, "message": m}
                                       for g, ar, m in res["findings"]]}, indent=1))
        return 0

    mi = res.get("module", {})
    if mi:
        print(f"{mi.get('Name')}  v{res.get('version')}  by {mi.get('Author')}")
        print(f"UUID {mi.get('UUID')}\n")

    order = {"BLOCKER": 0, "SHOULD": 1, "NOTE": 2}
    for g, area, msg in sorted(res["findings"], key=lambda f: order[f[0]]):
        print(f"{g:<8} [{area}]")
        for line in re.findall(r".{1,88}(?:\s|$)", msg):
            if line.strip():
                print(f"         {line.strip()}")
    n_block = sum(1 for g, _, _ in res["findings"] if g == "BLOCKER")
    n_should = sum(1 for g, _, _ in res["findings"] if g == "SHOULD")
    print(f"\n{n_block} blocker(s), {n_should} should-fix, "
          f"{len(res['findings']) - n_block - n_should} note(s)")
    if not n_block:
        print("No blockers. Publishable once the should-fixes are judged acceptable.")
    return 1 if n_block else 0


if __name__ == "__main__":
    raise SystemExit(main())
