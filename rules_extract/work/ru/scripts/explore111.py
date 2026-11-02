
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
OUT = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru'
TEXT = OUT + r'\text'
def load(fn):
    t = open(TEXT+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
P = load('PHB2024ru.full.txt')
i = P.find('Список черт')
seg = P[i:i+120000]
lines = [l.strip() for l in seg.split('\n')]
entries = []
k=0
while k < len(lines):
    l = lines[k]
    m = re.match(r'^(«?[А-ЯЁ][^»]{1,50}?)\*?\s+(Универсальные|Происхождение|Эпический дар|Боевой стиль)$', l)
    if m:
        entries.append((m.group(1).strip(), m.group(2).strip())); k+=1; continue
    # split columns
    m2 = re.match(r'^(.*?)\s+(Универсальные|Происхождение|Эпический дар|Боевой стиль)$', l)
    if m2 and not l.startswith('Черта') and len(m2.group(1))<60 and not m2.group(1).startswith('*'):
        entries.append((m2.group(1).strip(), m2.group(2).strip()))
    k+=1
seen=set(); res=[]
for ru,cat in entries:
    if ru in ('Черта',) or ru.startswith('*'): continue
    if ru not in seen and len(ru)>2:
        seen.add(ru); res.append((ru,cat))
    if len(res)>95: break
print(len(res))
for r in res: print(r)
json.dump(res, open(OUT+r'\_feat_ru.json','w',encoding='utf-8'), ensure_ascii=False, indent=0)

