# Stronger than the majority sweep: a field that NEVER appears on an entry of
# this type in the shipped data is a field the engine does not read there.
import re, glob, collections
from pathlib import Path
def parse(p):
    out={}; cur=None
    for l in Path(p).read_text(encoding='utf8',errors='replace').splitlines():
        m=re.match(r'^new entry "([^"]+)"',l)
        if m: cur={'_n':m.group(1)}; out[m.group(1)]=cur; continue
        if cur is None: continue
        m=re.match(r'^type "([^"]+)"',l)
        if m: cur['_t']=m.group(1); continue
        m=re.match(r'^data "([^"]+)"',l)
        if m: cur.setdefault('_f',set()).add(m.group(1))
    return out
seen=collections.defaultdict(collections.Counter)
for f in glob.glob('C:/Modding/bg3_unpacked/*/Public/*/Stats/Generated/Data/*.txt'):
    for e in parse(f).values():
        seen[e.get('_t')].update(e.get('_f',()))
print("FIELD-ON-WRONG-TYPE SWEEP\n")
n=0
for f in glob.glob('bg3/OathOfAvernus/Public/OathOfAvernus/Stats/Generated/Data/*.txt'):
    for e in parse(f).values():
        t=e.get('_t'); pool=seen.get(t)
        if not pool: print(f"  ?? type {t}: no shipped entries - NOT CHECKED"); continue
        for fld in sorted(e.get('_f',())):
            if pool[fld]==0:
                n+=1
                where={k:v[fld] for k,v in seen.items() if v[fld]}
                print(f"  {e['_n']:34s} data \"{fld}\" - ZERO uses on {t}; lives on {where}")
print(f"\n{n} field(s) on the wrong entry type ({sum(len(v) for v in seen.values())} field/type pairs measured)")
