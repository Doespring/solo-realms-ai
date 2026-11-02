
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
P = open(base+r'\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
P = re.sub(r'-\n','',P); P = re.sub(r'\n+','\n',P)
i=P.find('Боеприпас\nВы можете')
print(i)
j=P.rfind('Двуручное',0,i)
print(P[i-1200:i-50])
print('count of 1к4:', P.count('1к4'))
i2=P.find('Мастерство')
print('Мастерство', i2)

