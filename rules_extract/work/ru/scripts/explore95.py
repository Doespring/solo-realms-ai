
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
P = open(base+r'\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
P = re.sub(r'-\n','',P); P = re.sub(r'\n+','\n',P)
i=P.find('Типы урона\nАтаки')
i=P.find('Тип Примеры')
if i<0:
    i=P.find('Ядовитый газ, любой яд')
print(P[i-600:i+400])
print('====== soak')
i=P.find('Куб [Область')
print(P[i-1500:i+100])

