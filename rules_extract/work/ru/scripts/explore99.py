
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt')
i=G.find('Мировоззрение Как описано')
if i<0: i=G.find('мировоззрение — это')
print(G[i-100:i+3000])
i=G.find('Репутация Репутация — это')
if i<0: i=G.find('Репутация — это необязательное')
print('======'); print(G[i-100:i+1800])

