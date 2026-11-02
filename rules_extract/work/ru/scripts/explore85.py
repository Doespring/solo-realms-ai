
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
S = load('SRD521.full.txt')
pages = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', S)
pg = {int(pages[k]): pages[k+1] for k in range(1,len(pages),2)}
for p in (90,91):
    print('=====',p); print(pg[p][:2600])

