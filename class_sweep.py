"""Sweep for three bug classes, each found by a LIVE TEST on 2026-09-08.

Every one of that day's bugs was a value or a name that PARSED FINE and that the engine
then ignored. Three questions, none of which any existing gate asks:

  1. VALUE NOT ATTESTED FOR THIS FIELD. `Comment` vs `Name`; an Icon name that does
     not exist in the game draws a blank square, which is exactly what was reported.
  2. IDENTIFIER RESOLVES NOWHERE. A status/spell/passive named in a functor that
     exists in neither the shipped data nor our own files is a silent no-op.
  3. FEATURE ON THE WRONG CARRIER. A passive doing what vanilla does with a
     clickable spell - reported today as "the auras are clickable, ours is not".
     Reported for a human to judge, never auto-flagged as a fault.
"""
import re, sys, glob, collections
from pathlib import Path

MOD = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
VAN = Path('C:/Modding/bg3_unpacked')
NL = chr(10)

def parse(p):
    out, cur = {}, None
    for l in Path(p).read_text(encoding='utf8', errors='replace').splitlines():
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
        if m: cur['_f'][m.group(1)] = m.group(2)
    return out

van, mine = {}, {}
for f in glob.glob(str(VAN / '*/Public/*/Stats/Generated/Data/*.txt')):
    van.update(parse(f))
for f in glob.glob(str(MOD / 'Public/*/Stats/Generated/Data/*.txt')):
    mine.update(parse(f))

print("BUG-CLASS SWEEP - values, identifiers, carriers")
print("  corpus: %d shipped entries" % len(van))
print("  mod:    %d entries" % len(mine))
if len(van) < 5000:
    print("  !! CORPUS NOT READ - this is not a pass.")
    raise SystemExit(2)
print()

# ---- 1. values not attested for that field ---------------------------------
# Single-token fields only. A free-text field (Description, Boosts, a functor
# string) has no vocabulary to check against and would be pure noise.
TOKEN_FIELDS = {'Icon', 'StatsFunctorContext', 'SpellStyleGroup', 'VerbalIntent',
                'Sheathing', 'AIFlags', 'ManagedStatusEffectType', 'HitAnimationType',
                'StatusType', 'SpellType', 'PreviewCursor', 'CastTextEvent',
                'TooltipAttackSave', 'StackType', 'TickType'}
LIST_FIELDS = {'Properties', 'SpellFlags', 'StatusPropertyFlags', 'StatusGroups',
               'RemoveEvents'}
vocab = collections.defaultdict(collections.Counter)
for e in van.values():
    for k, v in e['_f'].items():
        if k in TOKEN_FIELDS and v:
            vocab[k][v.strip()] += 1
        elif k in LIST_FIELDS:
            for tok in re.split(r'[;,]', v):
                if tok.strip(): vocab[k][tok.strip()] += 1

n1 = 0
for name, e in sorted(mine.items()):
    for k, v in sorted(e['_f'].items()):
        if k not in vocab or not v:
            continue
        toks = [v.strip()] if k in TOKEN_FIELDS else [t.strip() for t in re.split(r'[;,]', v) if t.strip()]
        for tok in toks:
            if vocab[k][tok] == 0:
                n1 += 1
                print('  [1] %-32s data "%s" value "%s" - ZERO shipped uses of that value'
                      % (name, k, tok))
print("  [1] %d unattested value(s) over %d field vocabularies%s"
      % (n1, len(vocab), NL))

# ---- 2. identifiers that resolve nowhere ------------------------------------
FUNC = re.compile(r"\b(ApplyStatus|RemoveStatus|HasStatus|UnlockSpell|UseSpell)\s*\(([^)]*)\)")
known = set(van) | set(mine)
# Status GROUPS are legal arguments to HasStatus/RemoveStatus and are not entries.
groups = set()
for e in list(van.values()) + list(mine.values()):
    for tok in re.split(r'[;,]', e['_f'].get('StatusGroups', '')):
        if tok.strip(): groups.add(tok.strip())
n2 = 0
for name, e in sorted(mine.items()):
    body = NL.join(e['_f'].values()) + NL + e['_f'].get('Passives', '')
    refs = set()
    for m in FUNC.finditer(body):
        for a in m.group(2).split(','):
            a = a.strip().strip("'\"")
            if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,}', a):
                refs.add(a)
    for tok in re.split(r'[;,]', e['_f'].get('Passives', '')):
        if tok.strip(): refs.add(tok.strip())
    # words that are functor keywords or entity selectors, not identifiers
    NOISE = {'SELF', 'TARGET', 'SWAP', 'OWNER', 'CAUSE', 'context', 'Source', 'Target'}
    for r in sorted(refs - known - groups - NOISE):
        if r.isupper() or r[0].isupper():
            n2 += 1
            print('  [2] %-32s references "%s" - not an entry here or in the corpus' % (name, r))
print("  [2] %d dangling identifier(s)%s" % (n2, NL))

# ---- 3. features delivered by the wrong carrier -----------------------------
# Reported, never failed. Today the aura was a passive that applied a permanent
# status on OnCreate; every shipped paladin aura is a FREE Shout at duration -1,
# so the player can click it and dismiss it. Anything with this shape deserves a look.
n3 = 0
for name, e in sorted(mine.items()):
    if e.get('_t') != 'PassiveData':
        continue
    fx = e['_f'].get('StatsFunctors', '')
    if e['_f'].get('StatsFunctorContext') == 'OnCreate' and ',-1)' in fx.replace(' ', ''):
        n3 += 1
        print('  [3] %-32s applies a PERMANENT status from OnCreate. Vanilla delivers'
              % name)
        print('      this shape as a free clickable Shout - check it should not be one.')
print("  [3] %d carrier(s) worth a look%s" % (n3, NL))

print("%d finding(s) in classes 1-2, %d note(s) in class 3" % (n1 + n2, n3))
raise SystemExit(1 if (n1 + n2) else 0)
