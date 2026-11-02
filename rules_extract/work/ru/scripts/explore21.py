
import re, sys
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
txt = open(base+'\\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
for kw in ['Тайная магия','Убеждение','Медицина']:
    k = txt.find(kw)
    print('###',kw,k)
k = txt.find('Навык Характеристика')
print(txt[k-200:k+1800] if k>0 else 'no header')
k2 = txt.find('Расследование')
print('Расследование at', k2)
print(txt[k2-900:k2+900])

