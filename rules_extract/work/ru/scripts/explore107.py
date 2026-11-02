
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
S = open(base+r'\SRD521.full.txt', encoding='utf-8').read().replace('\r\n','\n')
S = re.sub(r'-\n','',S); S = re.sub(r'\n+','\n',S)
pages = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', S)
pg = {int(pages[k]): pages[k+1] for k in range(1,len(pages),2)}
def pgn(pos): return S[:pos].count('===== [стр.')
for kw in ['D20 Test','Bloodied','Weapon Mastery','Heroic Inspiration','Armor Training','Stabil',]:
    ms=[pgn(m.start()) for m in re.finditer(kw,S)]
    print(kw, ms[:12])
for kw in ['Stable','stable','Death Saving Throw']:
    print(kw, S.count(kw))
i=S.find('Death Saving Throw')
print(S[i-200:i+900])

