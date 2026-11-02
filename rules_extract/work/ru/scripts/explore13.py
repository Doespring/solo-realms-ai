
import re, sys, collections
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load_pages(name):
    txt = open(base+'\\'+name+'.full.txt', encoding='utf-8').read()
    parts = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', txt)
    return {int(parts[i]): parts[i+1] for i in range(1, len(parts), 2)}
def clean(t):
    t = t.replace('\r\n','\n')
    t = re.sub(r'-\n', '', t)
    t = re.sub(r'[ \t]+',' ', t)
    t = re.sub(r'\n+', '\n', t)
    return t
P = load_pages('PHB2024ru')
tags = collections.Counter()
entries = []
out=[]
for p in range(355, 389):
    t = clean(P.get(p,''))
    for line in t.split('\n'):
        line=line.strip()
        m = re.match(r'^(.{2,90})\s*\[(Состояние|Действие|Бонусное действие|Реакция|Отношение|Угроза|Область|Прочее|Состояния|Действия)\]\s*$', line)
        if m:
            tags[m.group(2)]+=1
            out.append((p, m.group(1), m.group(2)))
print(tags)
for o in out: print(o)

