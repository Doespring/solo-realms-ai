
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
txt = open(base+'\\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt)
for kw in ['Дробящий урон','Школы магии','Виды существ','Огненный урон','тип урона']:
    for m in list(re.finditer(kw, txt))[:2]:
        i=m.start()
        print('###',kw,i)
        print(txt[max(0,i-400):i+900].replace('\n',' '))
        print()

