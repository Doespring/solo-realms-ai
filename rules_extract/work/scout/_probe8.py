
import sys, os, re, glob
root=sys.argv[1]
src=os.path.join(root,'solo','rule_knowledge.py')
t=open(src,encoding='utf-8').read()
i=t.find('TERMS={'); j=t.find('}', i)
print(t[i:i+2200])
print('--- defs ---')
for m in re.finditer(r'^def [a-z_]+\(.*', t, re.M): print(m.group(0)[:140])
print('--- rulesets api defs ---')
t2=open(os.path.join(root,'solo','rulesets.py'),encoding='utf-8').read()
for m in re.finditer(r'^    def [a-z_]+\(.*|^def [a-z_]+\(.*|^class .*', t2, re.M): print(m.group(0)[:140])
