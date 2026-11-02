
import re, sys, json
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
txt = open(base+'\\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
idx = txt.find('Атлетика')
print('first Атлетика at', idx)
print(repr(txt.max(0,idx-300) if idx>=0 else 'NONE'))

