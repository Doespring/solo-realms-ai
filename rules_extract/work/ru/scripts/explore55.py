
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
P = open(base+r'\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
P = re.sub(r'-\n','',P); P = re.sub(r'\n+','\n',P)
for kw in ['Общий язык','Языки','Редкие языки','Стандартные языки']:
    idxs=[m.start() for m in re.finditer(kw,P)]
    print(kw, len(idxs), idxs[:5])
i=P.find('Стандартные языки')
print(P[i-1500:i+2500] if i>0 else 'NA')

