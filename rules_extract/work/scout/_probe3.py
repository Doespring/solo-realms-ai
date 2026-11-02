
import json, glob, os, collections, sys
root = sys.argv[1]
# 1) v2 rules: ids, kinds, implementations, handler counts
ids=[]; kinds=collections.Counter(); impl=collections.Counter(); cls=collections.Counter(); ops=collections.Counter()
files = sorted(glob.glob(os.path.join(root,'rules_data','v2','**','*.rules.json'), recursive=True))
for f in files:
    d=json.load(open(f,encoding='utf-8'))
    for r in d.get('rules',[]):
        ids.append((os.path.relpath(f,root), r['id'], r.get('kind'), r.get('classification',{}).get('mode'), r.get('implementation'), (r.get('readiness') or {}).get('status')))
        kinds[r.get('kind')]+=1; impl[r.get('implementation')]+=1; cls[r.get('classification',{}).get('mode')]+=1
        for o in r.get('formal_operations',[]): ops[o['op']]+=1
print('v2 rule files:', len(files))
print('v2 rules total:', len(ids))
print('kinds:', dict(kinds)); print('impl:', dict(impl)); print('classification:', dict(cls)); print('ops:', dict(ops))
print('per-file:', collections.Counter(x[0] for x in ids))
# sample ids by file
by=collections.defaultdict(list)
for row in ids: by[row[0]].append(row[1])
for f,v in by.items(): print(' ',f, len(v), v[:6])
