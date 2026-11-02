
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
P = load('PHB2024ru.full.txt')
out=[]
for m in re.finditer(r'Подкласс\s*«([^»]{0,60})»', P):
    pg = P[:m.start()].count('===== [стр.')
    out.append((pg, re.sub(r'\s+',' ',m.group(1))))
for o in out: print(o)

