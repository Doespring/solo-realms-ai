
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt')
for kw in ['иммунитет','сопротивление','уязвимость','Опасности\n','Горение','Кислот','Сл. 15','Стадии','уровень истощения','Истощение']:
    ms=list(re.finditer(kw,G))
    print('###',kw,len(ms))
    for m in ms[:3]:
        print('   ',G[max(0,m.start()-120):m.end()+120].replace('\n',' '))

