
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
G = open(base+r'\GM2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
G = re.sub(r'-\n','',G); G = re.sub(r'\n+','\n',G)
pages = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', G)
pg = {int(pages[k]): pages[k+1] for k in range(1,len(pages),2)}
print('GM pages', len(pg))
for p in (53,54,30,31,38,39,40,41,56,57):
    t = pg.get(p,'')
    print('=====',p)
    print(t[:1400])

