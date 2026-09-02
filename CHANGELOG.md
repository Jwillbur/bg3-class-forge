# Changelog — Class Forge

A scaffolding and audit framework for Baldur's Gate 3 class and subclass mods.

This file covers the forge only. It is published to
[`Jwillbur/bg3-class-forge`](https://github.com/Jwillbur/bg3-class-forge) by
`git subtree split` from the repository the forge is developed in, so the public copy is
regenerated rather than edited and the two cannot drift. **Dates are the dates the work
landed upstream**, which is why a release here can be days newer than the last change.

---

## 2026-09-01

- **⭐ `pak_audit.py` is part of the forge now, not part of one mod.** It lived in
  `bg3/Warpblade/tools/`, so the check that answers *"is what shipped what we wrote?"*
  gated exactly one mod. Every scaffolded mod is gated by it from this release.
- **⚠ Moving it exposed a real bug class, and it is worth naming: a shared tool that
  resolves its target from `cwd` or from a hardcoded directory depth audits the wrong
  thing silently.** The mod-local version located the workspace with
  `Path(__file__).resolve().parents[3]`, which only holds inside the repo it was written
  in. The tool now DISCOVERS a mod by its `forge.json`, and `$FORGE_CONFIG` overrides it.
  A tool that names its mod is a tool that will one day audit somebody else's.
- **`pak_audit` reports UNREADABLE as severity UNKNOWN and exits 2**, no longer as an
  error. A pak the tool cannot open is not a pak that is wrong, and conflating them makes
  a missing Divine look like a broken build.
- **NEW `divine.py` - one probed Divine resolver.** `build.ps1` had trusted
  `convert-loca`'s exit code, which Divine returns **0** for even when it converted
  nothing. It now probes Divine's actual capability and verifies the artifact exists.
  `-DivinePath` is honoured instead of silently falling through to discovery, so the
  negative control now proves something.
- **NEW `forge.py probe`** - scaffolds the smallest loadable mod that grants ONE named
  passive or spell. The point is the bottleneck it attacks: verifying a single engine
  assumption had been costing a full build, a launch and a character reroll, which is
  why live verification kept not happening.
- **NEW `build_acceptance.py` (26 controls)** - eight real builds driven against fake
  Divine shims, so the build's own failure paths are exercised rather than assumed.

---

## 2026-08-31

- **The build now audits the PAK, not just the source.** Every gate the forge runs read
  the workspace — the files you wrote. Nothing read the artifact the game actually
  opens. The build had been printing an archive listing at the pack step for weeks and
  **nothing consumed it**, which is the same shape as a drift report nobody reads: a
  check whose output no one reads is not a check, it just looks like one on the way
  past.

  A new `tools/pak_audit.py` in the parent repo extracts the built pak and asserts four
  things against source — every file that should ship did, nothing extra rode along,
  every shipped file is byte-identical, and `dist/` matches the deployed copy. The
  staging contract is expressed as a RULE (`SHIP_DIRS`, `NEVER_SHIP`) rather than a
  copied file list, so it and `build.ps1` cannot drift apart quietly.

  It caught an ordering bug in its own wiring on the first build: it audited the
  DEPLOYED pak *before* the deploy step, comparing against the previous build. Now split
  into `--dist` before the deploy and `--deployed` after — which is also the only
  ordering where "is the game running what we just built" means anything. That question
  was previously answered by checking a file with the right NAME existed.

- **The build now checks whether the mod tells the player the truth.** Every gate here
  asks whether a mod WORKS; none asked whether it is HONEST, and a tooltip is the only
  contract a player gets. A validator is perfectly happy with a spell that promises a
  status its functors never apply, because both halves are well-formed on their own.

  A new `tools/tooltip_audit.py` in the parent repo compares the declared tooltip fields
  against the real `ApplyStatus` and `DealDamage` calls across all four functor blocks,
  and blocks the build. Two real findings on the first clean run of the reference mod,
  both a description saying something the data did not do.

## 2026-08-30

- **`build.ps1` had a literal TAB character where an escaped `t` was intended**, so a
  `Test-Path` guard looked for a mangled path that never exists — and the tool-audit
  step it guarded **silently never ran inside the build**. Rewritten with forward
  slashes throughout, which carry no escape meaning and cannot be re-corrupted the same
  way. Worth stating plainly for anyone adapting this script: a path guard that fails
  closed and prints nothing is indistinguishable from a step that passed.

- **Three checks added to the pipeline**, in the order they landed: a fight simulator as
  a REPORTING step, its `--lint` half as a BLOCKING one, and a tool audit as reporting.
  The split is deliberate and is the rule to copy, not the tools. The simulator's
  scenarios encode *desired behaviour*, so a known-and-accepted defect would wedge every
  build — which is how a gate gets `-Skip`'d within a week and then guards nothing. Its
  lint rule encodes no design opinion, so there is nothing to legitimately disagree with
  and it blocks.

- **The self-test is skipped when its fingerprint is unchanged**, saving roughly two
  minutes per rebuild. The stamp hashes every `.py` the suite runs OR exercises, so a
  tool edit invalidates it — verified in both directions. **Only a GREEN stamp is
  skippable**, so a failing suite can never be cached into a pass.

## 2026-08-30 (earlier) — `load_order_audit.py`

- **Parallel pak reads: 12.3s → 2.2s, a 5.5x end-to-end speedup.** The measurement came
  first and it refuted the idea it was meant to support. Divine process start is 48ms of
  a 71ms list-package call, so the "spawning is the cost" diagnosis was right — but the
  audit makes 2–3 calls per pak, about 126 spawns of a 12.3s run, and an in-process
  LSLib binding would have bought only ~2x. Threading the spawns we already make
  measured 6.4x at 8 workers and 9.0x at 16, **with no pythonnet, no .NET coupling and
  no second code path bound to an assembly version**. Threads rather than processes,
  because the work is a blocking subprocess wait. `--jobs 1` restores serial.

- **A determinism bug the parallel run exposed, and the more valuable half of that
  work.** `audit()` iterated `set(entries)` and set-derived `.items()`, and Python
  randomises string hashing per process — so **two identical SERIAL runs already
  disagreed on finding order**, verified before any change was made. An audit that
  reshuffles cannot be diffed against yesterday's. Five iterations sorted; serial and
  parallel output are now byte-identical to each other and to themselves. The
  determinism fixture's names are chosen so sorted order differs from insertion order,
  because a fixture that passes either way proves nothing.

- **Duplicate and cycle detection**, mined from a competing auditor
  (`Nemix3D/bg3-load-order-optimizer`) — two checks we genuinely lacked out of six
  sources reviewed.
  - **Duplicates:** position is built by `enumerate()`, so a mod listed twice in
    `modsettings` collapses to its LAST index, and every ordering verdict in the file is
    then measured against a position the first copy does not have. Counted by name and
    by uuid separately.
  - **Cycles:** "loads later" is a per-edge verdict, and a cycle is the one shape where
    every edge reports cleanly and no order satisfies them all.

- **Still not a sorter, and that is a decision.** A well-resourced competitor
  (`Moonie8t7/VOLO`) trains its masterlist on 81 real load orders across 10,113 mods and
  reports 65.5% agreement against a 50.7% random baseline. A sorter that good is right
  about two thirds of the time. We should not ship a worse one as a side feature.

## 2026-08-29 (later)

- **`build.ps1` now runs `feature_sig.py`.** Its own header comment had named that tool
  among the checks whose order matters, alongside `validate.py` and the board - and it
  was never actually invoked. The comment was the only evidence anyone had, and comments
  are not executable. It is a WARNING, not a throw: feature drift is normal
  mid-development, and a gate that fails the build on every legitimate edit gets
  `-Skip`'d within a week and then guards nothing.
- **The pack step drops `.png` from the STAGE.** The game reads the `.DDS`; a generator's
  editable png masters were being packed alongside. On Warpblade that was 462,336 ->
  215,116 bytes, 53% smaller, with zero content change. Dropped from the stage only -
  the pngs stay in the workspace, because regenerating a DDS needs them.
- **The pack step refuses fewer than 20 staged files.** A staging step that silently
  staged nothing would have produced a confident, empty, perfectly valid pak.

## 2026-08-29

- **`load_order_acceptance.py` now declares a UTF-8 stdout.** It prints a star
  character, and on a cp1252 console (Git Bash on Windows) that is not a mangled
  glyph — it is a hard `UnicodeEncodeError` that kills the harness partway through.
  The identical line is fine in PowerShell, which is why this class of bug hides from
  whoever tests in the wrong shell. It had already taken out `--help` in seven tools
  upstream, and was reintroduced three times in a single day.

  A new `tools/encoding_gate.py` in the parent repo now refuses any tool that lacks
  the guard **and** emits characters cp1252 cannot encode, and the project's
  `selftest.py` discovers it automatically — so this cannot silently return.

  Nothing about the Forge's behaviour changed; the fix is six lines beside the
  imports, and the 32 load-order controls still pass.

## 2026-08-28

- `bump_version.py` — version bumping for any forge mod, including the fourth
  (`build`) field for a change too small to be a patch. Reads `forge.json` from the
  current directory rather than from the tool's own location, because the forge sits
  beside no mod.
- **Fixed: `scaffold` left `forge.json` one directory above the tree it described**, so
  `modconfig.find()` — which walks upward from the caller — resolved every path one level
  too high and no forge tool could find a mod the forge had just built. The fixtures did
  not catch it: `make_fixtures.py` builds them in the correct layout, which is a layout the
  real scaffold path never produced.

## 2026-08-26

- `load_order_audit.py` — nine checks over a BG3 load order, built because reading mod
  names is guesswork. The two that matter and that no human finds by eye:
  **`using "X"` inside `new entry "X"`** (the mod patches whatever defines X and must load
  after it) and **Osiris GOAL file collisions** (two mods writing the same goal means the
  loser's story never runs).
- Osiris rules are **additive**, so shared *events* are normal and are deliberately not
  reported; shared **flags** are the real signal. Unlisted paks that overwrite vanilla
  paths are **overrides, not faults** — that is how they work.
- Identical definitions across mods are not a conflict, and are no longer reported as one.

## 2026-08-25

- **Phase 2 complete: every tool reads `forge.json`.** `scaffold` emits **12 files**,
  including a full spell chain (spell, action resource, spell list, levelmap, and the
  progression rows that grant them), a placeholder class icon in all four real sizes, a
  `DESIGN.md` and a `build.ps1` shim. A freshly scaffolded mod passes the validator with
  0 errors and builds itself.
- **`fixtures/` — ten mods, nine broken on purpose plus a healthy control**, generated
  from the forge's own templates so they cannot drift into testing a shape the generator
  stopped producing. All nine defects are caught, control clean, and
  `fixture_matrix_acceptance.py` asserts each is caught **by the right check**.
- The fixtures earned their keep immediately: the forge had been shipping an invented
  `meta.lsx` field, `StartLevelName`, which appears in **zero** vanilla metas. The real
  one is `StartupLevelName`. Well-formed XML, plausible value, no error.
- `init` asks the four questions balance actually needs.
- Licensed **GPL-3.0-or-later**.
