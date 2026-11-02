
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    txt = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt); return txt
P = load('PHB2024ru.full.txt')
pages = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', P)
pg = {int(pages[k]): pages[k+1] for k in range(1,len(pages),2)}
for p in (201,202):
    print('=====',p); print(pg[p][:3200])

