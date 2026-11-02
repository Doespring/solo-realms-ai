
import json, sys, os
root=sys.argv[1]
p=os.path.join(root,'integration/nat20/playable/fighter-standard-array-2024-v1.json')
print(open(p,encoding='utf-8').read()[:3600])
print('=== campaign.schema top ===')
s=json.load(open(os.path.join(root,'integration/nat20/playable/campaign.schema.json'),encoding='utf-8'))
print(list(s.keys()), s.get('$id'))
defs=s.get('$defs') or s.get('definitions') or {}
print('defs:', list(defs.keys())[:60])
print('required:', s.get('required'))
print('props:', list((s.get('properties') or {}).keys()))
for k in list(defs.keys())[:6]:
    print(k, list(defs[k].get('properties',{}).keys()), defs[k].get('required'))
