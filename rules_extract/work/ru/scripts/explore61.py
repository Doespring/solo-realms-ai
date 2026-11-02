
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt')
for kw in ['Сложность','Сл.','Ст\\.','модификатор характеристики','Бонус владения','Класс Защиты','класс защиты','Владени[ея]','ВЛАДЕНИЕ','навык','Навык', 'Хиты','хиты','Опыт','Показатель опасности','ПО ']:
    print(kw, len(re.findall(kw,G)))
print('====')
for kw in ['Сложность','Класс Защиты','Владени']:
    for m in list(re.finditer(kw,G))[:4]:
        print(kw,'|',G[max(0,m.start()-100):m.end()+100].replace('\n',' '))

