
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    txt = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt); return txt
P = load('PHB2024ru.full.txt')
print([m.group(1) for m in re.finditer(r'Подкласс\s*\n?«([^»]+)»', P)][:40])
print('Бард pages:', [m.start() for m in re.finditer(r'\nБард\n', P)][:10])
i = P.find('Коллегия')
print(P[i-300:i+300] if i>0 else 'no Коллегия')

