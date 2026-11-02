
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
G = open(base+r'\GM2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
G = re.sub(r'-\n','',G); G = re.sub(r'\n+','\n',G)
for kw in ['Анализ','Уклонение','ПВ']:
    for m in list(re.finditer(r'\b'+kw+r'\b', G))[:25]:
        s=G[max(0,m.start()-70):m.end()+50].replace('\n',' ')
        print(kw,'|',s)
    print('---')

