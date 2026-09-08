"""Field-on-wrong-type audit.

Stronger than a majority sweep: a field that appears ZERO times on an entry of
this type in the shipped data is a field the engine does not read there. A
majority sweep compares an entry to its peers OF THE SAME TYPE, so it can never
see a field that does not belong to the type at all - which is how
AVERNUS_ZARIELS_FAVOR shipped as a decorative icon on 2026-09-08, carrying
StatsFunctorContext/Conditions/StatsFunctors on a StatusData.

Covers BOTH surfaces:
  stats .txt  - `data "Field"` keyed by the entry's `type`
  .lsx        - `<attribute id=...>` keyed by the containing `<node id=...>`

KNOWN BLIND SPOT: meta.lsx. Vanilla modules ship 5 meta files and none carries a
<TargetModes>/<Target> node, so this audit cannot judge it - but Warpblade, which
is released and working on the Nexus, carries the same node. Mod meta.lsx is an
authoring convention the vanilla modules are not examples of. Checked by hand
2026-09-08; do not chase the NOT CHECKED lines for meta.lsx again.

It prints its own coverage. ZERO IS NOT ABSENCE: a clean run over a corpus that
was not read is not a pass, so every uncheckable type/node is named out loud.
"""
import re, sys, glob, collections
from pathlib import Path

# 50, not 10. At 10 this tool invented 17 findings against Warpblade from an
# EffectInfo pool of 18 nodes in 9 files - vanilla's real effect banks are binary
# .lsf that this corpus does not decode, so "absent from 18 nodes" is not evidence.
# A threshold that turns thin coverage into confident findings is worse than no gate.
LSX_MIN = 50
MOD = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
VAN = Path('C:/Modding/bg3_unpacked')

# ---------------------------------------------------------------- stats .txt
def parse_stats(p):
    out = {}; cur = None
    for l in Path(p).read_text(encoding='utf8', errors='replace').splitlines():
        m = re.match(r'^new entry "([^"]+)"', l)
        if m: cur = {'_n': m.group(1)}; out[m.group(1)] = cur; continue
        if cur is None: continue
        m = re.match(r'^type "([^"]+)"', l)
        if m: cur['_t'] = m.group(1); continue
        m = re.match(r'^data "([^"]+)"', l)
        if m: cur.setdefault('_f', set()).add(m.group(1))
    return out

# ----------------------------------------------------------------- lsx nodes
def parse_lsx(p):
    """node id -> set of attribute ids. Comments are stripped: a comment inside
    a node splits its attribute run and fakes an absence."""
    t = re.sub(r'<!--.*?-->', '', Path(p).read_text(encoding='utf8', errors='replace'), flags=re.S)
    out = collections.defaultdict(list)
    for m in re.finditer(r'<node id="([^"]+)">\s*((?:<attribute [^>]*/>\s*)+)', t):
        out[m.group(1)].append(set(re.findall(r'id="([^"]+)"', m.group(2))))
    return out

van_stats = collections.defaultdict(collections.Counter)
for f in glob.glob(str(VAN / '*/Public/*/Stats/Generated/Data/*.txt')):
    for e in parse_stats(f).values():
        van_stats[e.get('_t')].update(e.get('_f', ()))

van_lsx = collections.defaultdict(collections.Counter)
lsx_seen = collections.Counter()
for f in glob.glob(str(VAN / '*/Public/*/**/*.lsx'), recursive=True) + glob.glob(str(VAN / '*/Mods/*/*.lsx')):
    for node, rows in parse_lsx(f).items():
        lsx_seen[node] += len(rows)
        for r in rows: van_lsx[node].update(r)

print("FIELD-ON-WRONG-TYPE AUDIT")
print(f"  corpus: {VAN}")
print(f"  read {sum(len(v) for v in van_stats.values())} field/type pairs over "
      f"{len(van_stats)} stats types, and {len(van_lsx)} lsx node kinds")
if not van_stats or not van_lsx:
    print("\n  \u26d4 CORPUS NOT READ - this is not a pass."); raise SystemExit(2)

findings = notchecked = 0
mine_stats = sorted(glob.glob(str(MOD / 'Public/*/Stats/Generated/Data/*.txt')))
mine_lsx   = sorted(glob.glob(str(MOD / 'Public/*/**/*.lsx'), recursive=True)) \
           + sorted(glob.glob(str(MOD / 'Mods/*/*.lsx')))
print(f"  mod: {MOD.name} - {len(mine_stats)} stats file(s), {len(mine_lsx)} lsx file(s)\n")

for f in mine_stats:
    for e in parse_stats(f).values():
        pool = van_stats.get(e.get('_t'))
        if not pool:
            notchecked += 1
            print(f"  ?? {e['_n']}: type {e.get('_t')} has no shipped entries - NOT CHECKED")
            continue
        for fld in sorted(e.get('_f', ())):
            if pool[fld] == 0:
                findings += 1
                where = {k: v[fld] for k, v in van_stats.items() if v[fld]} or 'NOWHERE - invented?'
                print(f"  {Path(f).name} {e['_n']}: data \"{fld}\" has ZERO uses on "
                      f"{e['_t']}; lives on {where}")

for f in mine_lsx:
    for node, rows in parse_lsx(f).items():
        pool = van_lsx.get(node)
        if not pool or lsx_seen[node] < LSX_MIN:
            notchecked += 1
            print(f"  ?? {Path(f).name} <{node}>: only {lsx_seen[node]} shipped node(s) - NOT CHECKED")
            continue
        for i, r in enumerate(rows):
            for a in sorted(r):
                if pool[a] == 0:
                    findings += 1
                    where = {k: v[a] for k, v in van_lsx.items() if v[a]} 
                    where = dict(list(where.items())[:3]) if where else 'NOWHERE - invented?'
                    print(f"  {Path(f).name} <{node}> #{i+1}: attribute \"{a}\" has ZERO uses "
                          f"on that node; lives on {where}")

print(f"\n{findings} field(s) on the wrong type, {notchecked} thing(s) NOT CHECKED")
if notchecked:
    print("  !! a NOT CHECKED line is not a pass - it is a gap.")
raise SystemExit(1 if findings else 0)
