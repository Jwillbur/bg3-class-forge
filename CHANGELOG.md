# Changelog — Class Forge

A scaffolding and audit framework for Baldur's Gate 3 class and subclass mods.

This file covers the forge only. It is published to
[`Jwillbur/bg3-class-forge`](https://github.com/Jwillbur/bg3-class-forge) by
`git subtree split` from the repository the forge is developed in, so the public copy is
regenerated rather than edited and the two cannot drift. **Dates are the dates the work
landed upstream**, which is why a release here can be days newer than the last change.

---

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
