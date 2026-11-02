
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
txt = open(base+'\\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt)
# find "Типы урона" glossary entry with table listing types - search 'Дробящий Тупые'
for m in re.finditer(r'Тип урона', txt):
    i=m.start(); s=txt[i:i+900]
    if 'Дробящий' in s:
        print(i, s.replace('\n',' ')); print('---'); 

