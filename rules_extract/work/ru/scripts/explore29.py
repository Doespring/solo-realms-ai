
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
txt = open(base+'\\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt)
for kw in ['Скверна','Эманация','Труднопроходимая местность','Критическое попадание','Типы урона','Инициатива']:
    i = txt.find(kw)
    print('###',kw,i)
    if i>0: print(txt[i-250:i+650].replace('\n',' '))
    print()

