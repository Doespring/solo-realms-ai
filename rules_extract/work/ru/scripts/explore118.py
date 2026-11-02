
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
P = open(base+r'\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
P = re.sub(r'-\n','',P); P = re.sub(r'\n+','\n',P)
for kw in ['Бонус владения к','Бонус владения (БВ)','Промах','Легендарные действия','Действия в логове','Сокровища','Врождённое«','колдовство','Совершать бросок Инициативы','Совершите','получает урон','получает 1','становится ','преуспеть в проверке','Свиток заклинания','Действия в логове','Логово','Логова']:
    print(kw, P.count(kw))

