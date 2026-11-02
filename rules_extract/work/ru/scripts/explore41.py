
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    txt = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt); return txt
S = load('SRD521.full.txt')
for m in re.finditer(r'Subclass:\s*([A-Z][A-Za-z \'\-]+?)\.{3,}|Subclass:\s*([A-Z][A-Za-z \'\-]+?)\n', S):
    print(m.group(1) or m.group(2))
print('---- other pattern')
for m in re.finditer(r'([A-Z][A-Za-z ]+Subclass|Subclass: [A-Z][^\n]{0,50})', S):
    t=re.sub(r'\s+',' ',m.group(1))
    if 'feature' not in t: print(repr(t))

