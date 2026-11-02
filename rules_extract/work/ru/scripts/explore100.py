
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt'); P=load('PHB2024ru.full.txt')
for kw in ['Анализ','Расследовани']:
    for m in list(re.finditer(kw,P))[:6]:
        print('PHB',kw,'|',P[max(0,m.start()-100):m.end()+60].replace('\n',' '))
print('#### GM Концентрация/Ритуал/Реакция/Бонусное действие')
for kw in ['Концентрац','Ритуал','Бонусное действие','Бонусным действием','Реакци','реакци']:
    print(kw, len(re.findall(kw,G)))
print('#### GM Заклинание уровни')
for kw in ['заговор','Заговор','ячейк','ячейк заклинани']:
    print(kw, len(re.findall(kw,G)))

