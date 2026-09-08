"""Sweep for three bug classes, each found by a LIVE TEST on 2026-09-08.

Every one of them PARSED FINE, passed every other gate here, and was then ignored by
the engine. Three questions nothing else asks:

  1. VALUE NOT ATTESTED FOR THIS FIELD ON THIS TYPE. The six spell lists used
     `Comment`, which appears 0 times in 358 shipped SpellList nodes. An `Icon` name
     that does not exist draws a blank square, which is what was reported that day.
  2. IDENTIFIER RESOLVES NOWHERE. A status or passive named in a functor that exists
     in neither the shipped data nor our own files is a silent no-op.
  3. FEATURE ON THE WRONG CARRIER. A passive doing what vanilla does with a clickable
     spell - "the paladin auras are clickable, ours is not". Reported as a NOTE for a
     human to judge; a gate that blocks on a design judgement gets deleted.

⚠ THREE DEFECTS FOUND IN THIS TOOL ON ITS FIRST RUN AGAINST RELEASED WARPBLADE, all of
which produced confident false findings. They are why the code below looks the way it
does - do not "simplify" any of them back:

  a) The vocabulary was keyed by FIELD NAME ALONE. `Properties` on a PassiveData is a
     flag list; `Properties` on an InterruptData is a functor string. Keyed together,
     every interrupt looked wrong. Vocabularies are per (type, field).
  b) The vocabulary was built from a dict keyed by ENTRY NAME, so an entry redefined in
     a later module silently replaced the earlier one and its values vanished from the
     vocabulary. `Spell_Evocation_BoomingBlade` is a real shipped icon that this tool
     called invented. Vocabularies are built while parsing, from every entry seen.
  c) `OBSERVER_OBSERVER`, `OBSERVER_SOURCE` and `OBSERVER_TARGET` are entity selectors
     in interrupt functors - 127 shipped uses - not status names.

And one legitimate exemption: a mod may SHIP ITS OWN icons under Mods/<name>/GUI/Assets.
Those are read off disk, because no corpus can attest them.
"""
import re, sys, glob, collections
from pathlib import Path

MOD = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
VAN = Path('C:/Modding/bg3_unpacked')
NL = chr(10)

# Icon is a GLOBAL asset namespace - an icon shipped for a spell is perfectly legal on
# a passive - so it is keyed by field alone, never by (type, field). And it can only ever
# be a NOTE: a mod may declare its own icons in a BINARY GUI/metadata.lsf atlas that
# nothing here can read. Warpblade's `Warpblade_WarpDice` is exactly that, and calling it
# invented would wedge a released mod's build over custom art.
GLOBAL_FIELDS = {'Icon'}
TOKEN_FIELDS = {'Icon', 'StatsFunctorContext', 'SpellStyleGroup', 'VerbalIntent',
                'Sheathing', 'AIFlags', 'ManagedStatusEffectType', 'HitAnimationType',
                'StatusType', 'SpellType', 'PreviewCursor', 'CastTextEvent',
                'TooltipAttackSave', 'StackType', 'TickType'}
LIST_FIELDS = {'Properties', 'SpellFlags', 'StatusPropertyFlags', 'StatusGroups',
               'RemoveEvents'}
# Entity selectors, not identifiers. See defect (c).
NOISE = {'SELF', 'TARGET', 'SWAP', 'OWNER', 'CAUSE', 'context', 'Source', 'Target',
         'OBSERVER_OBSERVER', 'OBSERVER_SOURCE', 'OBSERVER_TARGET'}


def parse(path, vocab=None, groups=None):
    """Entries by name, and - crucially - vocabulary accumulated PER ENTRY SEEN.
    Returning only the dict would lose every value of a redefined entry: defect (b)."""
    out, cur = {}, None
    for l in Path(path).read_text(encoding='utf8', errors='replace').splitlines():
        m = re.match(r'^new entry "([^"]+)"', l)
        if m:
            cur = {'_n': m.group(1), '_f': {}}
            out[m.group(1)] = cur
            continue
        if cur is None:
            continue
        m = re.match(r'^type "([^"]+)"', l)
        if m: cur['_t'] = m.group(1); continue
        m = re.match(r'^using "([^"]+)"', l)
        if m: cur['_u'] = m.group(1); continue
        m = re.match(r'^data "([^"]+)" "(.*)"\s*$', l)
        if not m: continue
        k, v = m.group(1), m.group(2)
        cur['_f'][k] = v
        if groups is not None and k == 'StatusGroups':
            for tok in re.split(r'[;,]', v):
                if tok.strip(): groups.add(tok.strip())
        if vocab is None: continue
        t = cur.get('_t')
        key = k if k in GLOBAL_FIELDS else (t, k)
        if k in TOKEN_FIELDS and v.strip():
            vocab[key][v.strip()] += 1             # per (type, field): defect (a)
        elif k in LIST_FIELDS:
            for tok in re.split(r'[;,]', v):
                if tok.strip(): vocab[key][tok.strip()] += 1
    return out


vocab = collections.defaultdict(collections.Counter)
groups = set()
van = {}
for f in glob.glob(str(VAN / '*/Public/*/Stats/Generated/Data/*.txt')):
    van.update(parse(f, vocab, groups))
mine = {}
for f in glob.glob(str(MOD / 'Public/*/Stats/Generated/Data/*.txt')):
    mine.update(parse(f, None, groups))

# Icons the mod ships itself. No corpus can attest these, and calling them invented is
# the tool being wrong about custom art.
own_icons = set()
for f in glob.glob(str(MOD / 'Mods/*/GUI/**/*'), recursive=True):
    p = Path(f)
    if p.is_file(): own_icons.add(p.stem)

print("BUG-CLASS SWEEP - values, identifiers, carriers")
print("  corpus: %d shipped entries, %d (type,field) vocabularies"
      % (len(van), len(vocab)))
print("  mod:    %d entries, %d icon file(s) of its own" % (len(mine), len(own_icons)))
if len(van) < 5000 or len(vocab) < 20:
    print("  !! CORPUS NOT READ - this is not a pass.")
    raise SystemExit(2)
print()

# A field is only checkable when its shipped values form an ENUM. `Properties` on an
# InterruptData holds functor strings - hundreds of distinct values - and treating that
# as a vocabulary flagged every interrupt Warpblade ships. 60 is comfortably above the
# largest real enum here and far below any free-text field.
ENUM_MAX = 60
n1 = notes = skipped = 0
for name, e in sorted(mine.items()):
    t = e.get('_t')
    for k, v in sorted(e['_f'].items()):
        pool = vocab.get(k if k in GLOBAL_FIELDS else (t, k))
        if not pool or not v.strip():
            continue
        if k not in GLOBAL_FIELDS and len(pool) > ENUM_MAX:
            skipped += 1
            continue
        toks = ([v.strip()] if k in TOKEN_FIELDS
                else [x.strip() for x in re.split(r'[;,]', v) if x.strip()])
        for tok in toks:
            if pool[tok] or tok in own_icons:
                continue
            if k in GLOBAL_FIELDS:
                notes += 1
                print('  [1N] %-33s data "%s" = "%s" - not a shipped name and not a'
                      ' file this mod ships. Fine if declared in the mod own icon'
                      ' atlas; a blank square if not.' % (name, k, tok))
                continue
            n1 += 1
            print('  [1] %-34s data "%s" = "%s" - zero shipped uses on %s'
                  % (name, k, tok, t))
print("  [1] %d unattested value(s), %d icon note(s), %d free-text field(s) skipped%s"
      % (n1, notes, skipped, NL))

FUNC = re.compile(r"\b(ApplyStatus|RemoveStatus|HasStatus|UnlockSpell|UseSpell)\s*\(([^)]*)\)")
known = set(van) | set(mine)
n2 = 0
for name, e in sorted(mine.items()):
    body = NL.join(e['_f'].values())
    refs = set()
    for m in FUNC.finditer(body):
        for a in m.group(2).split(','):
            a = a.strip().strip("'\"")
            if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,}', a):
                refs.add(a)
    for tok in re.split(r'[;,]', e['_f'].get('Passives', '')):
        if tok.strip(): refs.add(tok.strip())
    for r in sorted(refs - known - groups - NOISE):
        if r[0].isupper():
            n2 += 1
            print('  [2] %-34s references "%s" - not an entry here or in the corpus'
                  % (name, r))
print("  [2] %d dangling identifier(s)%s" % (n2, NL))

n3 = 0
for name, e in sorted(mine.items()):
    if e.get('_t') != 'PassiveData':
        continue
    if (e['_f'].get('StatsFunctorContext') == 'OnCreate'
            and ',-1)' in e['_f'].get('StatsFunctors', '').replace(' ', '')):
        n3 += 1
        print('  [3] %-34s applies a PERMANENT status from OnCreate. Vanilla ships this'
              % name)
        print('      shape as a free clickable Shout - check it should not be one.')
print("  [3] %d carrier(s) worth a look%s" % (n3, NL))

print("%d finding(s) in classes 1-2, %d note(s)" % (n1 + n2, notes + n3))
raise SystemExit(1 if (n1 + n2) else 0)
