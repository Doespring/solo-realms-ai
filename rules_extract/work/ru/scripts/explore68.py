
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt'); P = load('PHB2024ru.full.txt')
for kw in ['Вампир','Скелет','Медведь','Огр','Дракон','Зверь','Простое оружие','Воинское оружие','рукопашн','дальнобойн','Лёгк','Средн','Тяжёл']:
    print('##',kw,'GM',len(re.findall(kw,G,re.I)),'PHB',len(re.findall(kw,P,re.I)))
for kw in ['Вампир','Простое оружие','Скелет']:
    for m in list(re.finditer(kw,G))[:2]:
        print(kw,'|',G[max(0,m.start()-100):m.end()+80].replace('\n',' '))
# subclass list in PHB chapter start for each class - check "Подкласс" appeared 47; find missing
names=set(re.sub(r'\s+',' ',n) for n in re.findall(r'Подкласс\s*\n?«([^»]+)»', P) if len(n)<60)
print(len(names))

