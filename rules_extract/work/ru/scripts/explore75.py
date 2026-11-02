
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
P = open(base+r'\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
P = re.sub(r'-\n','',P); P = re.sub(r'\n+','\n',P)
pages = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', P)
pg = {int(pages[k]): pages[k+1] for k in range(1,len(pages),2)}
print(pg[237][:2600])
print('==== spell sample')
i=P.find('Волшебная стрела\n')
print(P[i-300:i+600] if i>0 else 'NA')
for kw in ['Магическая стрела','Волшебная стрела']:
    print(kw, len(re.findall(kw,P)))

