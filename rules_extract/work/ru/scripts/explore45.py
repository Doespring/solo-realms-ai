
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    txt = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt); return txt
P = load('PHB2024ru.full.txt')
for kw in ['Свойства искусности','Свойства оружия','Тяжёлое','Лёгкое','Дальнобойное','Бое\b']:
    j = P.find(kw)
    print('###',kw,j)
i = P.find('Свойства искусности')
print(P[i-300:i+3500])

