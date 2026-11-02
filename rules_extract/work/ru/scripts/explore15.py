
import re, sys
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load_pages(name):
    txt = open(base+'\\'+name+'.full.txt', encoding='utf-8').read()
    parts = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', txt)
    return {int(parts[i]): parts[i+1] for i in range(1, len(parts), 2)}
def clean(t):
    t = t.replace('\r\n','\n')
    t = re.sub(r'-\n', '', t)
    t = re.sub(r'\n+', '\n', t)
    return t
P = load_pages('PHB2024ru')
t = clean(P[23])
i = t.find('Навыки')
print(t[i:i+1400])
print('### SKILL LIST')
for p in range(23,26):
    tt = clean(P[p])
    if 'Навык' in tt:
        j = tt.find('Навык\n')
        print('page',p, tt[j:j+900] if j>=0 else '')

