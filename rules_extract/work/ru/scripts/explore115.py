
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt'); P = load('PHB2024ru.full.txt'); S=load('SRD521.full.txt')
for kw in ['Никакое','Длинный отдых','Короткий отдых','Долгий отдых','испытани','проверк','преимуществ','Помеха','Опыт','ПО ','КЗ']:
    print('GM',kw, len(re.findall(kw,G)))
print('---- PHB')
for kw in ['роль Мастера','Мастер описывает','Формула проверки']:
    print('PHB',kw, len(re.findall(kw,P)))

