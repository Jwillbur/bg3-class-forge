# Class Forge — a bootstrap prompt for building a BG3 class or subclass with Claude Code

**How to use this:** paste this entire document as your first message to Claude Code in an
empty folder. It will interview you, build its own tooling, then build your class.

You do not need to know how BG3 modding works. You do need to answer questions about the
class you want.

---

## PART 0 — WHAT YOU ARE, CLAUDE

You are building a **Baldur's Gate 3 class or subclass mod**, headless — no Toolkit, no
Creation Kit, no GUI editor. Everything is text files, a packing tool, and the game.

This document is a transplanted playbook. Every rule in Part 4 was paid for with a failed
build or a wasted play session on another project. **Treat them as findings, not opinions.**
Where a rule cites a measurement, the measurement was taken against real shipped game data.

Your job runs in four phases, in order:

1. **Interview** the user (Part 1). Do not build anything yet.
2. **Set up** the workspace and unpack the game data (Part 2).
3. **Build your tools first** (Part 3). This feels like a detour. It is not — see Part 4.
4. **Build it**, iterating with the user's live tests (Part 5).

Work in small, verifiable steps. Prefer measuring the game's own data over recalling what
D&D or the wiki says. **You will be wrong about BG3 specifics that you feel confident
about** — the engine is idiosyncratic and under-documented, and the corpus is the only
authority.

---

## PART 1 — INTERVIEW THE USER FIRST

Ask these in small batches (3–5 at a time), not as a wall. Use plain language; the user is
not expected to know engine terms. Record every answer in `DESIGN.md` as you go — that file
becomes the spec you build against and the thing you re-read when you lose the thread.

### Round 1 — the shape of it
1. **⭐ Is this a SUBCLASS of an existing class, or a whole NEW class?** Ask this first;
   it changes the architecture more than anything else they will say.
   - *Subclass* — it must insert itself into a vanilla class's progression, which is what
     Compatibility Framework exists for (see Part 2).
   - *New class* — it owns its own progression and typically needs **no dependencies at
     all**. Existing generated-class mods ship exactly this way.
2. **Which base class** does the subclass belong to, or which class is the new one closest
   to? (Fighter, Rogue, Cleric, Warlock…)
3. **What is the fantasy in one sentence?** "A knight who borrows time", "a rogue who
   fights their own shadow". Do not accept a list of mechanics here — you want the pitch.
4. **What level range?** BG3's campaign caps at **12**. Level 13–20 requires the player to
   also install a level-uncap mod. Ask which they are targeting, and tell them 12 is the
   safe default and 20 is what most Nexus class mods do.
5. **Is this for personal use or public release?** This changes how much effort goes into
   icons, localisation, compatibility and permissions.

### Round 2 — the engine of the class
6. **Does it have a resource?** (Superiority-dice-like pool, a charge, a stance, a stack
   that builds.) If yes: how many, and does it refresh on **Short Rest** or **Long Rest**?
7. **What is the signature action** — the thing you press that makes this subclass feel
   like itself? Describe what happens on screen.
8. **Is there a choice list** — pick 2 of 6 "techniques/manoeuvres/blessings"? If yes, how
   many exist and how many does a character get? *(Design note worth telling them: picks
   ÷ total is the replayability dial. 4 of 5 means two characters play almost identically;
   4 of 8 means they barely overlap.)*
9. **What does it do that its parent class cannot?** If the honest answer is "more damage",
   push back gently — that is a weapon, not a subclass.

### Round 3 — the numbers
10. **Which levels get features?** Default to the base class's own subclass levels — for
   Fighter that is 3, 7, 10, and (uncapped) 15 and 18. Confirm rather than assume.
11. **What is the closest official subclass** to compare against? You will use it as the
    balance anchor and it settles most arguments. ("Roughly a Battle Master" is enough.)
12. **Anything explicitly off the table?** No summons, no flight, no invisibility, etc.

### Round 4 — practicalities
13. **Where is BG3 installed?**
14. **Do they have LSLib / Divine.exe?** (Needed to pack and unpack. If not, tell them to
    get LSLib from GitHub — it is the standard community tool — and where to put it.)
15. **Mod name and author name**, for the metadata.
16. **Do they want custom art**, or are vanilla icons fine to start? Vanilla icon names
    work immediately and look borrowed; custom art is a day's work. Starting on vanilla
    and swapping later is a perfectly good plan — tell them that.
17. **Can they record a short video clip** if something looks wrong in game? For visual
    bugs a five-second clip is worth ten paragraphs, because "it plays too late" and "it
    plays at the old position" read the same in text and are different bugs.
18. **Do they want a build dashboard** (Part 3.8)? It costs an hour and pays for itself the
    first time they ask "what state is this in?"

**After the interview:** write `DESIGN.md`, read it back to the user as a short summary,
and get a yes before building. Flag anything you believe the engine cannot do — but check
the corpus before saying it cannot, because your instinct here is unreliable.

---

## PART 2 — WORKSPACE AND GAME DATA

```
<mod>/
  Mods/<ModName>/meta.lsx          # identity + version
  Public/<ModName>/
    ClassDescriptions/             # the subclass entry
    Progressions/                  # what you get at which level
    Lists/                         # selectable-passive lists
    Stats/Generated/Data/*.txt     # passives, spells, statuses
    Levelmaps/                     # values that scale with level
    MultiEffectInfos/              # VFX wiring (optional)
  Localization/English/<Mod>.xml   # every visible string
  tools/                           # what you build in Part 3
  docs/                            # queues + handover (Part 3.9)
  DESIGN.md
  CHANGELOG.md
```

**Unpack the game data before anything else.** You need `Shared.pak`, `Gustav.pak`,
`GustavX.pak` (and their `Dev`/`X` variants) extracted somewhere stable — this is your
corpus. Without it you are guessing, and Part 4 is largely a list of what guessing costs.

### `meta.lsx` — copy the FIELD NAMES out of vanilla, do not type them

**Open a shipped `meta.lsx` (`Shared/Mods/Shared/meta.lsx`) and match its `ModuleInfo`
attribute set exactly.** Do not reconstruct it from a tutorial or from memory. The game
reads these by name, an attribute it does not recognise is simply not there, and the file
is still perfectly well-formed XML either way — so nothing warns you.

**This bit us, in this very generator.** Its template emitted `StartLevelName`. The real
field is **`StartupLevelName`**, and `StartLevelName` appears in **zero** vanilla
`meta.lsx` files. A single missing letter, no error message, and the failure lands
*before character creation* — at New Game or the difficulty screen, where the campaign
loads — which is nowhere near the class data you would be staring at.

For a class or subclass add-on:

- `Type` is **`Add-on`**. If it says `Adventure`, the game treats your mod as a campaign
  and tries to load a level that is not there when the player hits New Game.
- `CharacterCreationLevelName`, `LobbyLevelName`, `MenuLevelName` and `StartupLevelName`
  are all **empty strings** — present, and empty. They are how a campaign declares its
  levels; you have none.
- `Folder` and `Name` must match your actual directory name (see §3.2's build gate).
- Every `Dependencies` entry must be a mod the player will actually have installed. A
  declared dependency that is missing takes the mod out of the load order, or the launch
  with it.

**Version64** packs as:
`(major << 55) | (minor << 47) | (revision << 31) | build`
So `1.0.0.0` = `36028797018963968`. Write a helper; do not hand-compute it twice.

**Registration depends entirely on the answer to interview question 1.**

- **A SUBCLASS of a vanilla class** has to insert itself into a progression it does not
  own. The reliable route is **Compatibility Framework** as a dependency: it scans every
  mod's `ClassDescriptions` and injects into Progressions, Feats and Lists at runtime,
  without overriding the base class. Overriding the base class's progression directly is
  what makes two subclass mods uninstallable together.
- **A NEW class owns its own progression and needs nothing.** Generated-class mods ship
  with *no Script Extender, no ImprovedUI, no Compatibility Framework*, referencing only
  base-game content — and if that class defines its own subclasses, those attach to its
  own progression, so they need nothing either.

The trade is real and worth putting to the user: a dependency-free new class installs for
anyone, but it will not appear as an option on Fighter. **Fewer dependencies is a feature**
— every one you add is a thing that can break on patch day and a reason someone skips your
mod. *Verify CF's current behaviour against its own source rather than trusting this
paragraph; it is exactly the kind of thing that changes.*

---

## PART 3 — BUILD YOUR TOOLS BEFORE THE MOD

Build these in order. Each is small. Skipping them is the single most expensive decision
available to you, because BG3 fails **silently**: a wrong field name does not error, it
just does nothing, and you find out after a 10-minute play session.

### 3.1 `corpus_index.py` — the one that matters most
Parse every unpacked `.txt` stats file into a searchable index of entries and fields.

Must support:
- `--entry <Name>` — dump one entry, including inherited (`using`) parents.
- `--find <Functor>` — every call site of a functor, **with the field name it appears in**.

**The field name is the point.** See rule 4.1.

### 3.2 `validate.py` — a build gate, not a linter
It must **exit nonzero** and refuse to pack. Checks, roughly in value order:

1. **Invented functors** — every functor you use appears somewhere in the corpus.
2. **⭐ Field attestation** — every functor you use appears in the corpus *in the field you
   used it in*. Warn loudly otherwise. This one check will save you multiple play sessions.
3. **Dangling references** — every status/passive/spell you name exists (yours or vanilla).
4. **Localisation** — every handle in stats exists in the XML, and vice versa.
6. **UUID sanity** — no duplicates, correct shape, referenced tables exist.
7. **Stale pak** — is the built pak older than any source file?
8. **⭐ Stale version** — has source changed since `Version64` was last bumped? Use **git**,
   not mtimes. *(This one exists because a project shipped an entire feature release under
   an unchanged version number and nobody noticed for days.)*

#### ⭐ 3.2b The half of that list that was missing, and why you would have missed it too

Read the eight checks above again and notice what they all have in common: **every one of
them answers "will my mod quietly do nothing." Not one answers "will the game die."**

That is not an oversight anyone would spot by reviewing the list. It is survivorship. The
project this document comes from never crashed BG3 — not once — so nothing was ever built
to catch a crash, and the list grew to fourteen checks with a hole in the shape of the
thing that never happened. **The first time this prompt was handed to someone else, their
mod crashed their game constantly.**

So build these too, from the start:

**Parse every `.lsx` as actual XML.** This is the one to do first, because the odds are
you have no XML parser anywhere in your toolchain at all. Everything reads `.lsx` with
`read_text()` and regexes it, and **a regex reads a broken file perfectly happily** — an
unclosed tag, a bare `&`, a mismatched quote, all sail through every other check and ship.
The game does not regex these files. It parses them, and it is not forgiving.
`ET.parse(path)` in a loop is four lines and it is the highest-value crash check you can
write.

**Prove `using` chains terminate.** Your dangling-reference check asks whether a parent
*exists*. It does not ask whether following the chain ever *stops*. `A using B, B using A`
satisfies the existence check twice over and takes the stats loader with it. Walk each
chain and error on a repeat. *(Positive-controlled on the real project: with a cycle
planted, the existence check stayed silent — which is exactly why this has to be its own
check rather than a stronger version of that one.)*

**The others worth having, all statically checkable:**
- **Duplicate UUIDs** anywhere in your files, and UUIDs referenced but never defined.
- **Progression rows naming a passive or spell that does not exist** — this crashes at
  level-up, which is the worst possible time, because the player has already committed.
- **`meta.lsx`'s `Folder` matching your actual pak directory structure.** Mismatch and the
  mod loads as something other than what you think you built.
- **Icon atlas metadata matching the icons you ship.** On this project a metadata mismatch
  produced a startup error that explained nothing.

**And the general habit, which outlives any list:** when you write a check, ask which of
the two questions it answers. If your whole list answers one of them, you have found your
blind spot rather than finished your work. A check list built from your own experience can
only cover failures you have personally survived.

### 3.3 `build.ps1` (or `.sh`) — pack and deploy
Runs `validate.py` as step 0 and aborts on error. Then packs with Divine and copies to
`%LOCALAPPDATA%\Larian Studios\Baldur's Gate 3\Mods\`.

**⚠ If Claude Code runs inside a packaged/sandboxed app**, writes to `%LOCALAPPDATA%` may
be redirected into a private virtual filesystem — the write appears to succeed, and reads
merge so verification *lies*. If you hit this: perform the copy via a shim launched with
`explorer.exe` (not a child of the sandbox) and have the shim write a report to a
non-redirected path that you read back. Verify the deployed file's **size**, not its
existence.

### 3.4 `release_check.py`
⭐ **Have it record the game build you validated against, and put that in the README.**
"Which patch did this last work on" is the first question anyone asks when a mod breaks
after a game update, and the first thing *you* will want when a validator that has always
passed suddenly doesn't. Your corpus tool already knows the build it read, so derive it
rather than asking anyone to remember.
Audits what a mod page shows: author, tags, description, dependencies, icon encoding,
localisation coverage, and **CHANGELOG's newest version vs the shipped version**.

### 3.5 `feature_sig.py` — catch "verified" going stale
Hash the files behind each feature. Record the signature **from git at the moment the
feature was verified** — never at today's HEAD. Then flag features whose files changed
afterwards.

Why it matters: "verified in play" is a claim about a *moment*. Nothing watches the code
after that moment, so a feature gets proven, rewritten twice, and still shows green.

**Must follow `using` inheritance.** A feature that inherits a parent's effect changes when
the parent changes, and a naive version reports it unchanged through three rewrites.

### 3.6 `fx_audit.py` — only if you ship custom VFX
Cross-references effects against **animation text keys**. See rule 4.5.

### 3.7 `balance_sim.py` — is it stronger than the thing it copies?
Rough damage/resource model per round versus the anchor subclass. It does not need to be
precise; it needs to stop "this feels strong" being the whole argument.

### 3.7b `rotation_sim.py` — does this SEQUENCE work?
Different question, and you will want it the moment the class has a resource.

`balance_sim` picks moves for you with a fixed rule ("use the big one if you can afford
it"). Real players run a **sequence**. So this takes a rotation — an ordered list of
actions, repeated across rounds — and reports damage per round, how often the pool ran
dry, and how many times each thing fired.

Add a `--trace` flag that prints one seeded fight line by line: round, action, hit or
miss, resource left. **That trace is where the answers actually come from.** On one
project it showed a "self-sustaining" loop slowly leaking resource, because a miss still
spends the cost — invisible in an averaged number, obvious in four lines of trace.

⭐ **Set the fight LENGTH before you trust a single number, and make the tool police
it.** The first run of this tool reported that a capstone built to sustain a loop added
**zero** damage to that loop, and that two candidate pool sizes scored **identically**.
Both looked like findings. Both were artefacts of a 4-round default: at ~2 resource a
round, an 8-point pool *cannot* be emptied in 4 rounds, so a feature whose entire job is
to postpone running dry had no way to matter. At 6 rounds the same comparison was **54.2
vs 47.7 damage per round, and the pool ran dry in 2% of fights versus 100%.** The user
caught it, not the tool, and it had already been written up as a conclusion.

The general rule, which is worth more than the specific number: **before believing a null
result, ask what would have had to HAPPEN for the two options to differ, then confirm your
test allowed it.** "No difference between A and B" is a finding, and it is also exactly
what a test that cannot see the difference prints.

So build the guard in. Report the enabling condition alongside the result - here, how
often the pool ran dry - and print a warning when it never occurred:

> *the pool never ran dry in any trial, so this fight did not test sustain at all. A
> refund or a larger pool CANNOT show a difference here.*

One thing to get right, because the first version of this guard got it wrong: key the
warning on the **observed** condition (`dry_rate == 0`), not on a theoretical maximum
spend. The theoretical version stayed silent on the exact case that caused the bug,
because the rotation being tested spent half its ceiling.

**Read every number out of the mod at run time** — costs, effect durations, thresholds,
the resource pool. And if a value cannot be parsed, **refuse to run**. Do not fall back on
a remembered number: a simulator confidently describing last week's build is worse than no
simulator.

Idea and shape borrowed from **gw2combat** (a Guild Wars 2 combat simulator). Turn-based
makes it easier than theirs — rounds instead of milliseconds.

⭐ **And say out loud that it is uncalibrated.** gw2combat is trusted because it is checked
against real in-game benchmarks. Yours is not checked against anything until you ask the
user for one real combat-log number and confirm the tool reproduces it. Until then, treat
the *gaps between options* as the finding and any single number as a guess with a decimal
point.

### 3.7c ⭐ The runtime layer — and the honest admission that everything above stops at the launcher

Read 3.1 through 3.7b again and notice what they have in common: **every one of them runs
before the game does.** They check the mod against the shipped data, against a schema,
against its own history. None of them can see a single thing that happens in play.

That gap is not academic. On the project this document comes from it was **the** bottleneck:
27 hand-run verification passes, each one a person casting a spell and reporting what they
saw. One feature failed twice, for two different causes, and both were things a machine
could have answered in seconds — *did the caster's resource counter go up*. Instead each
attempt cost a whole play session and a day of turnaround.

Three community tools close it. **None of them require changing your mod.**

**A combat-log capture, and this is the one to install first.** `Combat Log Log`
(`github.com/xiphiasrex/bg3-combatloglog-mod`) subscribes to the engine's own hit stream
with `Ext.Entity.OnCreateDeferred("HitResultEvent", ...)` and writes it out as JSON.
`!clstart` in the Script Extender **server** console, fight, `!clprint`. Per hit you get
the `SpellId`, the damage rolls **keyed by damage type** (so a custom damage rider arrives
already separated from the weapon roll, with dice size, count and natural roll), the saving
throws with **the actual DC that was rolled against**, `Originator.PassiveId` naming which
passive caused the hit, plus AC, advantage, criticals and resistances. Status applications
come through as their own rows.

Read that list again with your own class in mind. **That is most of a verification queue,
answered by one file.** It is also how you finally calibrate the simulator from 3.7b — you
no longer need the user to squint at a combat log, and because the capture carries the real
AC and roll bonuses, a disagreement can be blamed on the model rather than on your assumed
defaults.

**A test framework.** `DribbleSpec` (`github.com/AtilioA/DribbleSpec`) is a Jest-shaped
harness for Script Extender Lua — `describe`/`test`, the usual four hooks, `expect`
matchers, `mockFn`/`spyOn`/`stub`, and entity-aware assertions for UUIDs, entities and
components. Register with `Mods.Dribbles.RegisterTestGlobals{...}`, run with `!dribbles` or
your own alias, filter by tag. It stays optional for players.

⚠ **Do not install this reflexively.** If your class is pure stats, progressions and
localisation — as many are — adopting it means standing up a Lua runtime inside a mod that
has none, purely for testing. That is a real decision with a real cost. Ask the user, and
frame it honestly: *a separate test-only pak that reads your mod keeps the shipped mod
clean; putting the harness inside it behind a flag is simpler but ships test scaffolding to
players.*

**An entity inspector.** `Scribe` (Nexus 9200) dumps a live entity plus chosen components
to JSON. A before/after of a character's `ActionResource` block is the shortest possible
proof that a resource refund landed — and it needs no Lua in your mod at all, which makes
it the cheapest of the three to reach for.

**The habit to take from all this**, whatever you install: when a live test fails, ask
whether the question could have been answered by *data* instead of by a person watching.
Usually it can, and the person's time is the scarcest thing on the project.

### 3.8 The build board (optional, recommended)
A script that renders one HTML page: tool status, queue counts, per-feature verification
state, recent commits. Serve it on `127.0.0.1` with buttons that POST a **tool key** (never
a command string) so the page cannot execute arbitrary input.

Tiles worth having, learned the hard way:
- **Unpushed/uncommitted commits** — the only state where work exists in one place.
- **Drift count** from `feature_sig`.
- **Coherence**: does your feature list still match the shipped stats? Both directions —
  *shipped but not listed*, and *listed but not shipped*.
- **Doc freshness**: how many commits have touched mod source since each rendered doc was
  updated. Without this the board confidently describes last week's build.
- **Blocked on you**: items whose next move is the human's, not Claude's.

**Every tile must be able to render "bad".** A tile that cannot go red is decoration.

### 3.9 Working docs
- `docs/work-live.md` — needs the game running.
- `docs/work-offline.md` — does not.
- `docs/session-handover.md` — prepend an entry per session; **never trim it**.
- `docs/context-primer.md` — current state only. **Hard-cap it at ~3 session entries** and
  archive the rest. It is the only file loaded in full every session, so its size is a tax
  forever. One project let it reach 53,000 characters (~13,000 tokens per session start).
- `docs/lessons.md` — one entry per bug worth remembering: signature, why it hid, fix
  pattern, and whether a scanner can catch it in future.

---

## PART 3.5 — ICONS, WHICH WILL EAT A DAY IF YOU LET THEM

Icons are the single most common place a first mod stalls. The game does not tell you what
is wrong; you get a blank square, or a startup error about missing texture metadata, and
no clue which of several separate systems you got wrong.

**There is more than one icon system, and they do not work the same way.**

### Class / subclass icons — plain files in sized folders
These are loose `.DDS` files, and **the folder decides the size**, not the filename:

```
Assets/ClassIcons/<Name>                 300 x 300   + metadata
Assets/ClassIcons/hotbar/<Name>          140 x 140   + metadata
AssetsLowRes/ClassIcons/<Name>           152 x 152   files only, NO metadata
AssetsLowRes/ClassIcons/hotbar/<Name>     72 x  72   files only, NO metadata
```

All four, same base name. Miss the low-res pair and it looks fine until someone plays on
lower settings. Add metadata to the low-res ones and you get errors.

The character-sheet icon and the hotbar icon are **different pictures**, not one resized —
the hotbar one usually needs the background and any frame stripped so it reads at 72px.

⚠ **The low-res pair is 152/72, not 150/70.** This file said 150/70 until
2026-09-02 while `forge.py` had emitted 152/72 since the first mod that shipped. The
code is the one that has been in a working game, so the code won. `icon_audit.py` and
`icon_build.py` both import `forge.py`’s `ICON_SIZES` rather than retyping them, so
there is now exactly one copy of these four numbers.

### Spell / passive icons — an atlas, not loose files
Vanilla ships **no loose files for spell icons at all**. Only 64×64 tiles inside an atlas.
Verify that yourself by listing the shipped `.pak`s before you believe any tutorial.

This matters because tutorials tell you to make 380×380 tooltip art and 144×144 controller
art. That advice is for art you drew from scratch. If you are matching vanilla, **ship the
atlas only** — upscaling a 64px source to 380 is strictly worse than what vanilla does.

⚠ **The wiki and working mods disagree on the wiring.** Copy from a mod that demonstrably
works in game, not from documentation. On one project the correct answers were:
- the UV map is `GUI/Icons_Skills.lsx`, **not** `Icons_<Mod>.lsx`
- registration is `Content/UI/[PAK]_UI/Icons_<Mod>.lsf`, **not** `_merged.lsx`
- the `.DDS` extension is **uppercase**

Those three cost a day. Check them against a current working mod, because they may have
changed again.

### Getting the art itself
**Do not install an image generator locally.** Write a good prompt and have the user run it
through whatever image tool they already use, then process the result. On one project a
single ChatGPT pass beat hours of local work.

What you *should* build is the **processing**: background removal, ring/frame stripping,
resizing to the four sizes, DDS encoding, and a side-by-side preview so the user can judge
it without launching the game.

If you recolour vanilla icons to your class palette, three things that went wrong first:
- **Minimal intervention wins.** Vanilla's glow structure is already right; only the hue is
  wrong. Rebuilding the glow looked worse every single time.
- **Measure masks on the final image, not the input.** Brightening first and thresholding
  the old values turned every icon white.
- **Hue is circular.** Blending 11° → 269° the short way goes through magenta; the long way
  goes through green and yellow, which is exactly the fringing that showed up on every
  glyph edge.

### ⚠ If you ever ship virtual textures, there is a ceiling with a lying symptom
Roughly **48 mods loading loose virtual textures at once** is the vanilla limit. Past it,
**textures turn black** — and the symptom points at whatever was installed most recently,
not at the count. Removing that mod does fix it, by dropping back under the ceiling, which
makes the wrong diagnosis reproduce cleanly.

Script Extender can register several textures to one slot via `VirtualTextures.json`, and
plenty of mods simply don't. `VT Audit` (Nexus 23124) scans paks for `.gts` files and
reports which ones count; `VT SMG` generates the merge mappings.

Most class mods ship plain DDS and never touch this. Check whether yours ships a `.gts`
before assuming you are clear, and **when a limit is suspected, count rather than bisect** —
a tool that counts across the whole load order answers in one pass what bisection answers
in a dozen, and answers correctly.

---

## PART 3.6 — ANIMATIONS AND EFFECTS, THE OTHER DAY-EATER

The engine will happily accept an effect that can never play, and say nothing.

### Borrow a whole package before building one
The fastest reliable route is to find a vanilla spell that already looks close to what you
want and reuse its whole effect package. Note in a comment which spell you borrowed from —
future you will need to know where the pieces came from.

### An animation slot is not an animation
The GUID in a spell's animation field is a **key into a per-character animation set**, not
a clip. The same key resolves to different clips for different races, body types and
weapons. So "it worked on my character" tells you less than you think.

### ⭐ Effects hang off named events inside a clip, and a missing name fails silently
Effects can be bound to text keys — named moments in the animation (`Cast`, a wind-up key,
a weapon-hit key). **If the clip that actually plays has no such key, the effect is simply
skipped.** No error. No log line.

Worse, it is *inconsistent*: the key exists in some weapon and race clips and not others,
so it works for you and not for the player who reported it.

**Build a text-key index early.** Walk the animation data, record which keys exist in which
clips, and **filter to playable races** — unfiltered, the data is mostly creatures and the
percentages are meaningless. Then, for every effect you key to a name, check what share of
the relevant clips actually carry it. Anything under about 70% will look broken to
somebody.

### Larian ships one node per hit key, and that is why
You will see vanilla effects with several near-identical entries keyed to different hit
names. That is not redundancy — exactly one fires per weapon rig. Copy the whole fan-out;
picking one is how you get an effect that works with a sword and not an axe.

### Timing beats: what plays when
- A **wind-up** key fires before the action resolves. Use it for a charge or a tell.
- **`Cast`** fires as the action resolves. Use it for the payoff.
- A field named for *preparing* a spell may play during **target selection** — while the
  player is still aiming, before they have committed. Verify which is which in game before
  building a sequence on it; getting this backwards is a whole class of "my effect plays at
  the wrong time" bug.

### The failure this produces, and how to recognise it
On one project a teleport strike played its departure effect *after* the character had
already gone. It took **four attempts** to fix, and three of those attempts were timing
theories. The real cause was that the effects were keyed to names the weapon clip did not
have, so they were falling through to the wrong moment entirely.

**If an effect plays at the wrong time, suspect a missing key before you suspect timing.**
And ask the user for a short video clip rather than a description — "it plays too late"
and "it plays at the old position" are the same sentence to a user and different bugs to
you.

---

### If you genuinely have to author one: the toolchain, and two things that will cost you a weekend
Everything above is about *reusing* Larian's animations, which is what you should do. If the
user insists on original animation, the working path is Blender plus
`github.com/AkELkADDS/Blender_addons_AkELkA` — **GR2 LAB — Body Animation** for clean raw
import/export with B-spline support (Blender 5.2 LTS or newer), alongside mirror
bones/weights tooling for asymmetric rigs and vertex-group filtering.

Two details worth more than the rest of this paragraph, both from the addon author, who
also ships an FBX importer and *still* says the first one:

- **Use glTF to import animations, not FBX.**
- **Expect jitter.** Animations pulled out of the game shake on import. That is normal and
  there is a dedicated *Animation Fixer* for it — it is not a sign of a bad export, and
  hand-smoothing keyframes chasing it is wasted time.

Say both of these to the user before they start, not after.

---

## PART 4 — THE RULES, AND WHAT THEY COST TO LEARN

### 4.1 ⭐ "The functor exists" is not "the functor works here"
BG3 functors are only valid in certain **fields**. A real functor in the wrong field is a
**silent no-op** — no error, no log line, nothing happens.

Real examples, all measured:
- `RestoreResource` never appears in `SpellSuccess` in shipped data. Put it there and your
  resource refund does nothing.
- `HasHPLessThan` is real, but never appears in `OnRemoveFunctors`.
- `ManeuverSaveDC()` resolves in `SpellSuccess` and `SpellRoll` **only** — never in a
  passive.

**Rule:** before writing any functor into a field, run `--find <Functor>` and confirm
vanilla uses it *in that field*. Zero hits means you are inventing it.

### 4.2 ⭐ `context.Source` is not always who you think
In a passive **granted by a status** (via the status's `Passives` field), `context.Source`
means the entity **carrying the status** — the victim, not the caster. Effects that need
the caster silently resolve against the wrong creature.

Measured: of 491 vanilla status-granted passives, **21 use a literal save DC and 0 use any
DC helper**. There is no scaling DC available in that position. If you need one, restructure
so the effect resolves somewhere the caster is known.

### 4.3 ⭐ Pass the context argument explicitly
In an `OnAttack`-style passive both attacker and target are in scope. Measured: **24 of 24**
vanilla passives in that situation pass `context.Source` or `context.Target` explicitly to
`HasPassive()`. The only vanilla omissions are single-entity contexts like `OnShortRest`.

Omitting it cost one project two failed play sessions on the same feature.

### 4.4 ⭐ Target keywords: `SELF` is not the default
`RestoreResource(WarpDie,1,0)` pays whoever the functor is aimed at — often the enemy.
`RestoreResource(SELF,WarpDie,1,0)` pays the caster. Same for `ApplyStatus(SELF,...)`.

### 4.5 Effects keyed to a missing animation text key never fire
Effects can be bound to named events in an animation (`VFX_Antic_01`, `Cast`,
`VFX_Slash_Hit_01`). If the clip that actually plays does not carry that key, the effect is
simply skipped — silently, and inconsistently across weapons and races.

Build an index of which text keys exist in which clips, **filtered to playable races**
(unfiltered, the data is mostly creatures). Then check coverage per key.

Also: `PrepareEffect` plays during **target selection**, not as a cast beat. Do not use it
for a cast flourish.

### 4.6 Scaling values belong in a levelmap
For anything that grows with level (die size, damage), use a levelmap series and reference
it as `LevelMapValue(<Name>)`. Do not branch on level inside functors — level tests inside
passives are attested only in `Boosts`.

If you need behaviour (not a number) to change with level, **grant a different passive at
each tier via the progression** and swap them. It is verbose and it always works.

### 4.7 ⭐ Positive-control every check you write
Make each check **fail on purpose once** before trusting it. If you cannot construct an
input that fails it, it is not a check.

Real failures this prevents:
- A test asserting a UI element was absent by checking a substring that also appeared in
  the stylesheet — it could never pass in the negative direction.
- A drift detector that reported drift correctly but could not report *absence* of drift.
- A coherence checker that could not detect the exact thing it was written for, because a
  filter excluded the case that mattered.


**And negative-control it too — prove it stays QUIET on correct work.** A check that fires
on healthy states is not merely noisy, it is worse than absent: it still looks like
coverage while teaching you to skim past it. Three of them on this project, all written in
good faith. One flagged eight verified features that were perfectly fine. One flagged every
routine version bump. **And one was the test harness itself** — its controls were built by
copying the real project files and mutating them, so the day a live document legitimately
changed, four controls reported failures against code that was correct.

That last one is worth sitting with, because the failure inverts: **a test coupled to live
data does not break when the code breaks, it breaks when the data changes.** It cries wolf
on correct work and goes silent on the case it was written for. Build fixtures, not copies.

### 4.8 ⭐ A skip is not a pass
If a check cannot run — missing tool, missing path, missing config — report it in its own
category. One harness recorded a skipped block as a PASS, and on another machine read
"19 passed" with ten checks silently unattempted.

### 4.9 Measure before you design
When a design question has a number attached, go get the number.
- "Which scope resolves file citations?" — measured across 249 real claims: 6% / 17% / 18%
  / 62% depending on scope. The obvious choice was the worst one.
- "Is our save DC like the official subclass's?" — the answer was a 5-point gap at high
  level that nobody had noticed.

### 4.10 After two failed attempts, stop and research
Two failures on the same problem means your model of it is wrong. Go to the shipped data,
the tool's own source, or the vendor's docs. A third guess is almost always wrong too, and
the fourth is worse.

### 4.11 Never claim "verified" without a live test
Static checks prove a build is *loadable*, never that it *works*. Only the user, in game,
can say a feature works — and their report is the evidence. Record it with a date.

### 4.12 Clean up inert artifacts immediately
When a design change makes a status, passive or variable unused, **delete it**. Leaving it
as "harmless" is how the next reader concludes it does something.

### 4.13 Do not write backslashes through a shell heredoc
`\b`, `\v`, `\s` get one backslash eaten and become invisible control characters. A regex
becomes silently inert; a documented path becomes a dead link that reads fine to a human.
Write the script to a **file** and run it.

### 4.14 Give your hand-maintained data files a shape contract
Any JSON you maintain by hand and then *render* — a feature list, a config — will
eventually get a typo'd enum or a duplicated id, and it will fail somewhere far away.
One project wrote `status: "unverified"` where the renderer's list had no such value; the
result was a `ValueError` deep inside page generation and a whole tab silently missing.

Write a small schema (required keys, enums, types, ranges, uniqueness) and enforce it in
your build gate. This is borrowed from **gw2combat**, a Guild Wars 2 combat simulator that
refuses to run until its build and encounter JSON validate against schemas — the same
reasoning as refusing to pack a mod that fails validation.

You do not need a schema library for this; the useful subset is a few dozen lines.

### 4.15 ⭐ A check list built from your own experience has a hole shaped like what never happened to you
Silent failure and hard failure are two different questions, and tooling grows toward
whichever one has bitten you. The project behind this document never once crashed the
game, so after months of work its validator had **fourteen checks and not one of them
asked whether the game would die** — a gap nobody noticed until this prompt was handed to
a stranger whose mod crashed constantly.

Sorting the existing checks into the two buckets took about a minute and made the hole
obvious. Do that, deliberately, whenever a check list feels finished:

- **Will it silently do nothing?** — wrong field, dangling reference, unreachable spell.
- **Will the game die?** — malformed XML, a `using` cycle, a duplicate UUID, a progression
  naming something that does not exist.

If everything you have is in one column, that is the finding.

### 4.16 Comment density is not documentation
Explain *why* a value is what it is, not what the line does. The comment that matters is
"this threshold is 5 because the debt stacks additively at 2 per application" — not
"applies the status".

---

## PART 5 — BUILDING, AND THE LOOP WITH THE USER

**Order:** metadata → registration (Part 2) → **verify it appears in character creation** →
resource → signature action → **verify in play** → the rest, one feature at a time.

Do not build six features and then test. Every play session is expensive to the user, so
batch what you need tested and give them a **numbered checklist** with what you expect to
happen for each item. Their answers go straight into the live queue with dates.

**When something does not work, the most likely causes, in order:**
1. A functor in a field vanilla never uses it in (4.1).
2. A context resolving against the wrong creature (4.2, 4.3).
3. A missing `SELF` (4.4).
4. An effect keyed to an animation key that clip does not have (4.5, Part 3.6).
5. An icon in the wrong folder, or missing its low-res pair (Part 3.5).
6. The pak did not actually deploy (3.3).

**Bump the version on every shipped change**, and keep the CHANGELOG's top entry matching
it. Gate this in `validate.py` rather than relying on memory.

**Every session, write a handover entry** with: what landed, what was investigated but not
resolved, what is still open, and **exactly one specific next action**.

---

## PART 6 — START HERE

Do not build anything yet. Begin with:

> "Before I build anything I need to understand the class you want. I'll ask in a few small
> rounds — plain language, no engine jargon needed. First four: which base class, what's the
> one-sentence fantasy, what level range are you targeting (12 is BG3's cap; 20 needs a
> level-uncap mod), and is this for yourself or for public release?"

Then work through Part 1, write `DESIGN.md`, confirm it, and proceed.

---

*Transplanted from a working BG3 subclass project. Every rule in Part 4 was learned by
getting it wrong first — several of them more than once, and at least two of them after
confidently reporting the opposite. Trust the corpus over your instincts, and make every
check fail once before you believe it.*
