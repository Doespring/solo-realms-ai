
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt'); P = load('PHB2024ru.full.txt')
terms = ['Вын','Тел','Сил','Лов','Инт','Мдр','Хар','КД','БМ','ПВ','ПО','Сопротивлени','Уязвимост','Невосприимчивост','Иммунитет','Тёмное зрение','тёмное зрение','Истинное зрение','слепое зрение']
for t in terms:
    print(t, len(re.findall(re.escape(t),G)), len(re.findall(re.escape(t),P)))
print('--- reconciliation: PHB vs GM pairs')
for a,b in [('Расследование','Анализ'),('Восприятие','Внимательность'),('Класс защиты','КД'),('Бонус владения','БМ'),('Показатель опасности','ПО'),('Сопротивление','Сопротивление'),('Иммунитет','Невосприимчивость'),('Окровавленный','Окровавленный')]:
    print(a,'PHB',len(re.findall(re.escape(a),P)),'| GM',len(re.findall(re.escape(b),G)))

