"""Is this function used in THIS place in the shipped game?

Every other gate here asks whether a name EXISTS. This one asks whether it belongs
where it has been put, which is the only question that mattered on 2026-09-08. Five
bugs shipped that day and every one was a real identifier in the wrong context:

  `HasPassive` in a spell's RequirementConditions   - zero shipped uses; inert.
  `SourceSpellDC()` in a weapon-triggered OnDamage  - no source spell to read, so the
                                                      save auto-failed and it burned
                                                      every time.

Two layers, because those two need different depths to separate:

  LAYER 1  (entry type, field) -> function
           `HasPassive` in (SpellData, RequirementConditions) is 0 in the corpus.

  LAYER 2  (StatsFunctorContext, field) -> function, for passives only
           `SourceSpellDC` in (PassiveData, StatsFunctors) is 2 - not enough to flag.
           In StatsFunctorContext OnDamage it is 0, while SavingThrow there is 35.
           The context is what discriminates, so the context is what gets measured.

⚠ ZERO IS ONLY A FINDING WHEN THE POOL IS BIG. A function absent from a vocabulary
built out of nine observations proves nothing. Pools below MIN_POOL are reported as
NOT CHECKED, never as clean - the same rule that made class_sweep stop inventing
findings against released Warpblade.

    py context_audit.py [mod_dir]
"""
import re
import sys
import glob
import collections
from pathlib import Path

MOD = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
VAN = Path('C:/Modding/bg3_unpacked')

# A vocabulary needs this many distinct functions before an absence means anything.
MIN_POOL = 12
# And the function itself has to be common enough that its absence here is a signal
# rather than a gap in the corpus.
MIN_GLOBAL = 20

FN = re.compile(r'([A-Za-z_][A-Za-z0-9_]{2,})\s*\(')
# Not functions: damage types, ability names and the like never take arguments in a
# way this pattern can tell apart, and DealDamage(2d6,Fire) is not a call to Fire.
SKIP = {'IF', 'and', 'or', 'not'}


def scan(paths, learn):
    """learn=True builds the corpus vocabularies; learn=False returns our entries."""
    pair = collections.defaultdict(collections.Counter)
    ctx = collections.defaultdict(collections.Counter)
    glob_ct = collections.Counter()
    mine = []
    for f in paths:
        cur = None
        for line in Path(f).read_text(encoding='utf8', errors='replace').splitlines():
            if line.startswith('new entry '):
                cur = {'name': line.split('"')[1], 'type': None, 'ctx': None, 'data': []}
                if not learn:
                    mine.append(cur)
                continue
            if cur is None:
                continue
            m = re.match(r'^type "([^"]+)"', line)
            if m:
                cur['type'] = m.group(1)
                continue
            m = re.match(r'^data "([^"]+)" "(.*)"\s*$', line)
            if not m:
                continue
            k, v = m.group(1), m.group(2)
            if k == 'StatsFunctorContext':
                cur['ctx'] = v.strip()
            fns = {x for x in FN.findall(v) if x not in SKIP}
            if not fns:
                continue
            if learn:
                for fn in fns:
                    pair[(cur['type'], k)][fn] += 1
                    glob_ct[fn] += 1
                    if cur['ctx']:
                        ctx[(cur['ctx'], k)][fn] += 1
            else:
                cur['data'].append((k, v, fns))
    return pair, ctx, glob_ct, mine


van_files = glob.glob(str(VAN / '*/Public/*/Stats/Generated/Data/*.txt'))
pair, ctx, glob_ct, _ = scan(van_files, True)
mine_files = sorted(glob.glob(str(MOD / 'Public/*/Stats/Generated/Data/*.txt')))
_, _, _, mine = scan(mine_files, False)

print("CONTEXT AUDIT - is this function used in THIS place?")
print("  corpus: %d (type,field) vocabularies, %d (context,field) vocabularies, "
      "%d distinct functions" % (len(pair), len(ctx), len(glob_ct)))
print("  mod:    %s - %d entries in %d file(s)" % (MOD.name, len(mine), len(mine_files)))
if len(pair) < 40 or not glob_ct:
    print("  !! CORPUS NOT READ - this is not a pass.")
    raise SystemExit(2)
print()

findings = notchecked = 0
seen_gap = set()
for e in mine:
    for k, v, fns in e['data']:
        for layer, key, label in (
                ('type', (e['type'], k), e['type']),
                ('ctx', (e['ctx'], k) if e['ctx'] else None,
                 'StatsFunctorContext ' + (e['ctx'] or ''))):
            if key is None:
                continue
            pool = (pair if layer == 'type' else ctx).get(key)
            if not pool or len(pool) < MIN_POOL:
                if key not in seen_gap:
                    seen_gap.add(key)
                    notchecked += 1
                    print('  ?? %-46s only %d function(s) known - NOT CHECKED'
                          % ('%s / %s' % (key[0], key[1]), len(pool or ())))
                continue
            for fn in sorted(fns):
                if pool[fn] or glob_ct[fn] < MIN_GLOBAL:
                    continue
                findings += 1
                print('  %s' % e['name'])
                print('      data "%s" uses %s() - ZERO shipped uses in %s,'
                      % (k, fn, label))
                print('      though %s() is used %d times elsewhere. Real function,'
                      % (fn, glob_ct[fn]))
                print('      wrong place: it will parse and do nothing.')

print()
print("%d finding(s), %d vocabulary(ies) NOT CHECKED" % (findings, notchecked))
if notchecked:
    print("  !! a NOT CHECKED line is a gap, not a pass.")
raise SystemExit(1 if findings else 0)
