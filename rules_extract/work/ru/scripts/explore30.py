
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    txt = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt)
    return txt
S = load('SRD521.full.txt')
i = S.find('Damage Types')
print(S[i-200:i+1500])
print('========= SCHOOLS')
j = S.find('School of Magic')
print(S[j-300:j+800] if j>0 else 'no school of magic')

