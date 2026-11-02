
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    txt = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt); return txt
P = load('PHB2024ru.full.txt')
pages = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', P)
pg = {int(pages[k]): pages[k+1] for k in range(1,len(pages),2)}
print('### FEATS p.200')
t=pg[203]
print(t[:3500])
print('### FEATS p.208 (styles/epic)')
print(pg[209][:2500])
print('### BACKGROUNDS p.175-178')
for p in (176,177,178):
    print('--- p',p); print(pg[p][:2500])

