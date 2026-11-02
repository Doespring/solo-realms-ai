
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt'); P = load('PHB2024ru.full.txt'); S=load('SRD521.full.txt')
for kw in ['Халат','Бард','Плут','Чародей','Колдун','Варвар','Жрец','Монах','Паладин','Следопыт','Друид','Воин','Волшебник']:
    print(kw,'GM',len(re.findall(kw,G)),'PHB',len(re.findall(kw,P)),'SRD?',kw)
for kw in ['Bard','Rogue','Sorcerer','Warlock','Barbarian','Cleric','Monk','Paladin','Ranger','Druid','Fighter','Wizard']:
    print('SRD',kw,len(re.findall(kw,S)))
print('##### GM subclass mention')
for kw in ['Подкласс','Архетип']:
    print(kw, len(re.findall(kw,G)))
for kw in ['Латный','Варвар']:
    for m in list(re.finditer(kw,P))[:3]:
        print(kw,'|',P[max(0,m.start()-80):m.end()+60].replace('\n',' '))

