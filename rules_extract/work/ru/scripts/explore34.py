
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
txt = open(base+'\\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt)
parts = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', txt.replace('=====','=====') )
print(len(parts))
i = txt.find('Описания классов')
print('описания классов', i)
# search for the class overview table header
for kw in ['Классы','Бард','Варвар']:
    pass
m = re.search(r'\n(Варвар|Барвар)\n', txt)
print(m)

