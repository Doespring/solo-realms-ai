
import re, sys
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load_pages(name):
    txt = open(base+'\\'+name+'.full.txt', encoding='utf-8').read()
    parts = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', txt)
    return {int(parts[i]): parts[i+1] for i in range(1, len(parts), 2)}
P = load_pages('PHB2024ru')
for p in range(27,33):
    t = P.get(p,'')
    print('===',p, t[:160].replace('\r\n',' / '))
t = P[29].replace('\r\n','\n')
print(len(t))
print(t[:3000])

