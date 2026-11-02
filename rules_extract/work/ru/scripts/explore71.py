
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
P = load('PHB2024ru.full.txt')
# find each occurrence of '«' with context, restricted to pos from earlier list
for m in re.finditer(r'«', P):
    s = re.sub(r'\s+',' ',P[max(0,m.start()-40):m.start()+60])
    if any(k in s for k in ['Путь','Коллегия','Домен','Круг','Клятва','Покровительство','Воин ','Чародейство','Мистический','Пси']):
        print(s)

