
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
res=[]
for p in range(18, 26):
    tt = clean(P[p])
    for kw in ['Атлетика','Акробатика','Навыки']:
        j = tt.find(kw)
        if j>=0:
            res.append('page %d kw %s idx %d :: %s' % (p, kw, j, tt[max(0,j-150):j+250].replace('\n',' ')))
            break
print('\n'.join(res))

