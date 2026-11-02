
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
S = load('SRD521.full.txt')
for x in ['Cleave','Graze','Nick','Push','Sap','Slow','Topple','Vex']:
    m=re.search(r'\n'+x+r' Mastery|Mastery\n'+x, S)
    print('###',x, bool(m))
i=S.find('Mastery Properties')
print(S[i-100:i+2200] if i>0 else 'no header')

