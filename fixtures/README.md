# Broken mods, on purpose

Nine minimal subclass mods. One is correct; eight each carry a single documented defect.
`manifest.json` is the machine-readable version — id, what's broken, the in-game symptom,
and which check ought to catch it.

```bash
py fixtures_acceptance.py     # are the fixtures still broken in the way they claim
py make_fixtures.py --check   # have the templates moved out from under them
```

## Why

Every crash-class check in this toolchain is a **guess**. Warpblade has never crashed BG3,
so nothing here was written by watching a crash — checks 15 and 16 were reasoned out from
the shape of the bug and shipped without either one ever catching something real. A check
list built from your own experience has a hole shaped like what never happened to you.

A fixture is the missing half: break a mod deliberately, write down the symptom, and a
checker can finally be **wrong in a way you can see**.

## The set

| id | severity | what's wrong |
|---|---|---|
| `healthy` | control | nothing — plain `forge.py scaffold` output |
| `xml-malformed` | crash | `Progressions.lsx` lost its closing `</region>` |
| `using-cycle` | crash | two passives `using` each other |
| `dangling-passive` | crash | grants a passive no stats file defines |
| `meta-invented-field` | crash | `meta.lsx` says `StartLevelName`; vanilla says `StartupLevelName` |
| `dependency-absent` | crash | `meta.lsx` needs a module that isn't installed |
| `unresolvable-parent` | crash | `ParentGuid` is well-formed and matches no class |
| `table-uuid-mismatch` | silent | `ProgressionTableUUID` ≠ `TableUUID` |
| `missing-loca-handle` | silent | a handle with no localisation entry |
| `duplicate-uuid` | silent | one UUID used for two records |

**`healthy` is the most important one.** A checker that flags the control is worse than no
checker at all, because it teaches you to skim past its output.

`meta-invented-field` is not hypothetical: **`forge.py` shipped that exact bug**, found while
chasing a crash at the difficulty screen. An invented attribute name is well-formed XML with a
plausible value, so every check we had walked straight past it.

**All nine are now caught, and the control stays clean.** That was not the plan — it is
what the measurement produced. Pointing the real `validate.py` at each fixture showed it
already caught **five** of the nine; the manifest's own estimate had said two. Four checks
were then written against the four it missed (17-20). `bg3/Warpblade/tools/fixture_matrix_acceptance.py`
runs that matrix on every self-test, and asserts not just that each defect is caught but
that it is caught **by the right check** — an exit code alone cannot tell you whether the
check you meant to test fired or something incidental tripped first.

The first run also found a bug in the validator rather than in a fixture: it crashed with
an unhandled `FileNotFoundError` on a mod with no `Levelmaps` file, because it had only
ever run against one mod that happened to have every optional file. A traceback tells
someone whose mod is fine that the *tool* is broken.

## Three rules they follow

**Generated, never hand-written.** `healthy` is literally what `forge.py scaffold` emits,
and each broken one is `healthy` plus one mutation. A hand-written fixture is a snapshot of
what the generator produced the day someone typed it out; the generator moves, the fixture
doesn't, and the checks end up proving themselves against a mod shape nothing produces any
more. `--check` fails if the templates drift.

**One break each, and everything else stays valid.** In particular all of them except
`xml-malformed` still parse as XML. If one didn't, an XML check would fire on it first and
the defect actually under test would never be reached — the run would pass while proving
nothing.

**Deterministic UUIDs.** Regenerating produces a byte-identical tree, so a diff shows the
mutation instead of nine fresh mods. That is the opposite of how a real mod works, and the
values here are `uuid5` of a fixed namespace: **test data, never lift one into a mod.** The
one exception is `ParentGuid`, which is read from real game data like everywhere else in
the forge — `unresolvable-parent` is the fixture that proves the difference matters.
