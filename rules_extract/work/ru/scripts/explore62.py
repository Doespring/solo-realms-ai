
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt')
for kw in ['БВ ','КЗ','бонус владения','класс защиты','Модификатор','спасбросок','Спасбросок','Инициатива','инициатива','проверка характеристики','Проверки характеристик']:
    print(kw, len(re.findall(kw,G)))
for kw in ['Сл. 15','Сл 15','Сложность 15']:
    print('X',kw,len(re.findall(re.escape(kw),G)))
# monster stat block in GM
i=G.find('ПВ 10')
print(G[i-600:i+600])

