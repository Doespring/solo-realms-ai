
import re, sys
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load_pages(name):
    txt = open(base+'\\'+name+'.full.txt', encoding='utf-8').read()
    parts = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', txt)
    return {int(parts[i]): parts[i+1] for i in range(1, len(parts), 2)}
P = load_pages('PHB2024ru')
G = load_pages('GM2024ru')
def show(pg, p, n=1400):
    return pg.get(p,'').replace('\r\n','\n')[:n]
print('##### PHB p11 Six abilities')
print(show(P,11,1600))
print('##### PHB p13 Advantage')
print(show(P,13,800))

