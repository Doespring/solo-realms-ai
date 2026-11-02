
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load_pages(name):
    txt = open(base+'\\'+name+'.full.txt', encoding='utf-8').read()
    parts = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', txt)
    return {int(parts[i]): parts[i+1] for i in range(1, len(parts), 2)}
def clean(t):
    t = t.replace('\r\n','\n'); t = re.sub(r'-\n','',t); t = re.sub(r'\n+','\n',t); return t
G = load_pages('GM2024ru')
t = clean(G[54])
i = t.find('Мировоззрение')
print(t[i:i+3000] if i>=0 else t[:3000])

