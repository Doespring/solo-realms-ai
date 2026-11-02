
import sys, os, re
root=sys.argv[1]
src=os.path.join(root,'solo','combat.py')
t=open(src,encoding='utf-8').read()
m=re.search(r'class DamageType.*?(?=\nclass )', t, re.S)
print(m.group(0)[:900] if m else 'no DamageType block')
print('--- Weapon class ---')
m=re.search(r'class Weapon\(.*?(?=\nclass )', t, re.S)
print(m.group(0)[:900] if m else 'none')
