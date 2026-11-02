
import sys, os, json, collections
root=sys.argv[1]
defs=os.path.join(root,'integration','nat20','playable')
cats=['weapons','armor']
# weapon-mastery list from any docs?
import re
for f in ['NAT20_STAGE2.md','NAT20_PLAYABLE_NEXT.md']:
    p=os.path.join(root,'docs',f)
    t=open(p,encoding='utf-8').read()
    for m in re.finditer(r'(?i)(mastery|мастерств\w* оружи)', t):
        print(f, t[max(0,m.start()-120):m.start()+200].replace(chr(10),' ')[:320]); break
print('--- skills count in nat20_content ---')
t=open(os.path.join(root,'solo','nat20_content.py'),encoding='utf-8').read()
i=t.find('CHECKS')
print(t[i:i+1500])
