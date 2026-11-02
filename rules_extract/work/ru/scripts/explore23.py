
import sys
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
g = open(base+'\\GM2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
k = g.find('Глоссарий')
print('glossary idx', k)
import re
for m in re.finditer(r'Содержание|Оглавление', g[:60000]):
    print(m.start(), repr(g[m.start():m.start()+40]))
print(g[:1200])

