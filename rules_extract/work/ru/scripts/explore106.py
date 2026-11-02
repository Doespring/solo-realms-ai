
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
S = open(base+r'\SRD521.full.txt', encoding='utf-8').read().replace('\r\n','\n')
S = re.sub(r'-\n','',S); S = re.sub(r'\n+','\n',S)
for kw in ['D20 Test','Stabilized','Simple','Martial','passive Perception','Passive Perception']:
    ms=list(re.finditer(kw,S))
    print('##',kw,len(ms))
    for m in ms[:3]: print('   ',repr(S[max(0,m.start()-80):m.end()+80].replace('\n',' ')))

