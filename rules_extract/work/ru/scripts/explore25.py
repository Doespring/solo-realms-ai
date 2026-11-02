
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
g = open(base+'\\GM2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
k = g.find('Приложение')
print(g[9900:13000])

