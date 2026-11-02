
import re, sys
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load_pages(name):
    txt = open(base+'\\'+name+'.full.txt', encoding='utf-8').read()
    parts = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', txt)
    return {int(parts[i]): parts[i+1] for i in range(1, len(parts), 2)}
P = load_pages('PHB2024ru')
def find_page(pg, needle, start=1, end=None):
    out=[]
    for p,t in sorted(pg.items()):
        if p<start: continue
        if end and p>end: break
        if needle in t.replace('\r\n','\n'): out.append(p)
    return out
print('Глоссарий правил pages:', find_page(P,'Глоссарий правил'))
print('Состояния page:', find_page(P,'Состояния'))
print('Ослеплённый:', find_page(P,'Ослеплённый'))
print('Ошеломлённый:', find_page(P,'Ошеломлённый'))
print('Сбит с ног:', find_page(P,'Сбит с ног'))
for p in find_page(P,'Типы урона'):
    print('Типы урона on page', p)

