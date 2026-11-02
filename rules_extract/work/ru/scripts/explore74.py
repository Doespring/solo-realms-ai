
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
P = open(base+r'\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
P = re.sub(r'-\n','',P); P = re.sub(r'\n+','\n',P)
for kw in ['1-й уровень:','1-й уровень заклинаний','уровня заклинания','Заговоры','1-го уровня','9-го уровня','Круг заклинаний','Дистанция','Дистанция:','Продолжительность','Компоненты','Время сотворения']:
    print(kw, len(re.findall(re.escape(kw),P)))
i=P.find('Уровень заклинания\n')
print(P[i-200:i+2200])

