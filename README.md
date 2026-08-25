# Class Forge

**Everything needed to start a BG3 class or subclass mod, in one folder you copy.**

```bash
py forge.py doctor      # is this machine set up at all
py forge.py classes     # the real vanilla classes, read from YOUR game data
py forge.py init        # a short interview -> forge.json
py forge.py scaffold    # -> a complete, loadable mod tree
```

| File | What it is |
|---|---|
| `forge.py` | The generator. Produces a subclass that **loads** before any design exists. |
| `FORGE.md` | The prompt. Hand it to an agent — the interview, the tool-build order, and 16 rules, each backed by something that actually went wrong. |
| `forge_acceptance.py` | Controls. Most of them assert a **refusal**. |
| `modconfig.py` | Where a mod's files live, read from its `forge.json`. Every tool in the toolchain uses it, so there is one codebase rather than a copy per project. |
| `fixtures/` | Nine mods broken on purpose, one defect each, plus the healthy control. **All nine are caught and the control stays clean** — measured, not assumed. |
| `make_fixtures.py` | Regenerates them from the templates above, so they cannot drift into testing a mod shape nothing produces any more. |
| `build.ps1` | The real build script for every mod made with this toolchain. Each mod gets a shim that finds it. |

---

## Why a generator and not just the prompt

However good a set of instructions is, it is still a set of instructions — and a
first-timer hand-writes LSX, mistypes a UUID, lifts a GUID out of a tutorial, and the game
dies at character creation with no message that means anything. The fastest way to stop
that is to not let them hand-write the plumbing at all.

So `scaffold` emits a subclass that already works: identity, class description, a
progression table, an inert placeholder passive, **a spendable resource with a spell that
costs it**, a spell list, a levelmap, a placeholder class icon, the localisation to name
it all, and a `DESIGN.md` holding the decisions you just made. **Empty of design, complete
of plumbing.** You get something in the game in the first session, and *then* you decide
what the class does.

## The thing it refuses to do

**It never invents a UUID, and it never ships one you found somewhere.**

- Every UUID it generates is fresh and recorded in `forge.json`, so nothing is copied and
  nothing collides.
- The one vanilla UUID it must reference — your parent class — is **read out of your own
  unpacked game data**. Not a table in the source. Not memory. If the unpacked data is not
  there, it **refuses to scaffold**.
- And it re-checks that GUID against the game at scaffold time rather than trusting
  `forge.json`, because a config can be hand-edited or copied between machines. A wrong
  `ParentGuid` validates cleanly, packs cleanly, and crashes at character creation.

That last refusal is the most valuable line in the tool. It is why `read_classes()` parses
the game instead of carrying a list of GUIDs someone typed once — a list that would also
rot the first time Larian shipped a class.

Everything it writes is parsed as XML before it tells you it succeeded. A generator that
emits a malformed file is worse than none, because the output arrives with a provenance
story attached.

## The order matters, and it is the actual lesson

1. **Scaffold, pack, and load the game.** Confirm the subclass appears with real text and
   not a raw handle — *before* writing content. Then when something breaks later, you know
   the plumbing was fine.
2. **Build the validator** (`FORGE.md` §3.2 and §3.2b). Sort its checks into *"will it
   silently do nothing"* and *"will the game die."* Most people only ever build the first
   column. That is how mods crash constantly. **Test it against `fixtures/`** — nine mods
   broken on purpose, one defect each. When that was first measured against a real
   validator it caught five of the nine; the four it missed became four new checks.
3. **Then design.** That is a conversation with your agent, not a thing a generator should
   guess at.

## License

**GPL-3.0-or-later.** Use it, change it, build your class with it — that is what it is for,
and nothing you MAKE with it is covered: your mod is yours.

The licence bites only if you distribute a modified version of **this tool**. Then it has
to stay open under the same terms, carry a note saying you changed it, and keep the
copyright notice — so the trail back to the original stays attached. `LICENSE` is the
full text.

## Not yet — and these are gaps, not principles

An earlier version of this file listed the following as things the forge deliberately
*doesn't* do. That was wrong, and worth correcting rather than quietly deleting: they are
things it does not do **yet**, and calling a limitation a design decision is how a tool
stops improving.

The goal is a class mod built with as little manual plumbing as possible. Measured against
that, here is the honest state.

| | Where it stands |
|---|---|
| **Spells** | ✅ Scaffolded. A spell that costs a resource, the resource itself, a spell list, a levelmap, and the progression rows that grant them. A freshly scaffolded mod passes the validator with **0 errors**. |
| **Class icon** | ✅ Scaffolded, in all four real sizes, with a `.png` beside each for texconv. Deliberately crude — it is meant to look temporary. ⚠ Not yet confirmed in game. |
| **Balance** | ✅ The interview asks the four questions a balance model needs — what this is the equivalent of, pool size, uses per turn, level range — and writes them into `forge.json` and a `DESIGN.md`. **Pricing them is still manual.** |
| **Packing** | ✅ `scaffold` writes a `build.ps1` that validates, compiles localisation, packs and deploys. It is a **shim** onto the forge's own build script, not a copy - a copy stops receiving fixes the moment it is made. |
| **Spell icons** | Not a generator's job. Point at the closest vanilla icon and replace it with your own art later. |

**All of it is done.** What is left is the part that was never a generator's job: pricing a feature against its comparison target, and art. Both are conversations, not commands.

## The one thing it should never do

**Decide what the class is.** A generator that guesses at a concept produces a class nobody
chose. Automate the execution completely; the design stays a conversation.
