
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
S = open(base+r'\SRD521.full.txt', encoding='utf-8').read().replace('\r\n','\n')
S = re.sub(r'-\n','',S); S = re.sub(r'\n+','\n',S)
i = S.find('Epic Boon Feats')
j = S.find('Equipment', i)
print(S[i:j][:3000])
print('....')
i2 = S.find('Feat Descriptions')
print(S[i2:i2+1200])

