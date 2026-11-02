
import re, sys
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
txt = open(base+'\\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
k = txt.find('Глоссарий правил')
# find glossary start marker: 'Условные обозначения'
k = txt.find('Условные обозначения')
print('glossary at', k)
seg = txt[k:]
heads=[]
for m in re.finditer(r'\n([^\n]{2,70})\n\n', seg):
    h = m.group(1).strip()
    if len(h)>1 and h[0].isupper() and not h.endswith('.') and not h.endswith(',') and '«' not in h and '@' not in h:
        heads.append(h)
print(len(heads))
print(' | '.join(heads[:200]))

