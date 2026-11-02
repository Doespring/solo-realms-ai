
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
P = open(base+r'\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
P = re.sub(r'-\n','',P); P = re.sub(r'\n+','\n',P)
for kw in ['Никакой','без проверки','нет необходимости','не требуется']:
    print('PHB',kw, P.count(kw))
    for m in list(re.finditer(kw,P))[:3]:
        print('   |',P[max(0,m.start()-120):m.end()+120].replace('\n',' '))

