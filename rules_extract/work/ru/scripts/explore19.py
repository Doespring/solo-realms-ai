
import re, sys
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
txt = open(base+'\\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
for m in re.finditer(r'Список навыков', txt):
    print('---', m.start())
    print(txt[m.start():m.start()+1500])
    break

