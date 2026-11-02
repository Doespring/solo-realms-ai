
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt'); P = load('PHB2024ru.full.txt')
for kw in ['Нокаутир','оглушительный удар','Смерть персонажа','смерт','Магическая школа','школа']:
    for m in list(re.finditer(kw,G))[:3]:
        print(kw,'|',G[max(0,m.start()-100):m.end()+100].replace('\n',' '))
for kw in ['Нокаутирование','Окровавлен']:
    for m in list(re.finditer(kw,P))[:2]:
        print('PHB',kw,'|',P[max(0,m.start()-120):m.end()+120].replace('\n',' '))

