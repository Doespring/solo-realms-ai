
import json, sys, os
root=sys.argv[1]
def keys(o, prefix='', depth=0, out=None):
    if out is None: out=set()
    if depth>3: return out
    if isinstance(o, dict):
        for k,v in o.items():
            out.add(prefix+k); keys(v, prefix+k+'.', depth+1, out)
    elif isinstance(o, list):
        for v in o[:2]: keys(v, prefix+'[].', depth+1, out)
    return out
for path in ['integration/nat20/imported/canonical/monsters/goblin-warrior.json',
             'integration/nat20/imported/canonical/classes/fighter.json',
             'integration/nat20/imported/canonical/items/longsword.json',
             'integration/nat20/imported/canonical/species/human.json',
             'integration/nat20/imported/canonical/features/second-wind.json']:
    d=json.load(open(os.path.join(root,path),encoding='utf-8'))
    print('==',path)
    print('  top keys:', list(d.keys()))
    print('  all keys:', sorted(keys(d))[:70])
