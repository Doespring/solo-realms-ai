
import sys, os, re
root=sys.argv[1]
t=open(os.path.join(root,'solo','combat.py'),encoding='utf-8').read()
i=t.find('DamageType')
print(t[max(0,i-200):i+600])
