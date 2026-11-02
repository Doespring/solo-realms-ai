
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
OUT = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru'
TEXT = OUT + r'\text'
S = open(TEXT+r'\SRD521.full.txt', encoding='utf-8').read().replace('\r\n','\n')
S = re.sub(r'-\n','',S); S = re.sub(r'\n+','\n',S)
PHB = open(TEXT+r'\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
PHB = re.sub(r'-\n','',PHB); PHB = re.sub(r'\n+','\n',PHB)
def pgn(T,pos): return T[:pos].count('===== [стр.')
# SRD feats that exist (heading followed by a category line)
cats = ['Origin Feat','General Feat','Fighting Style Feat','Epic Boon Feat']
found = {}
for m in re.finditer(r'\n([A-Z][A-Za-z’\' \-]{2,40})\n((?:Origin|General|Fighting Style|Epic Boon) Feat[^\n]*)', S):
    if m.start() < S.find('Equipment\nCoins'):  # restrict to feats chapter
        found[m.group(1).strip()] = (m.group(2).strip(), pgn(S, m.start()))
print(len(found))
srd_feats = sorted(found.items())
json.dump(srd_feats, open(OUT+r'\_srd_feats.json','w',encoding='utf-8'), ensure_ascii=False, indent=0)
for k,(c,p) in srd_feats: print(f'{p}\t{k}\t{c}')
# PHB feat heading pages for description (not the list table)
PHBfeats = json.load(open(OUT+r'\_feat_rows.json',encoding='utf-8'))
print('PHB feats found:', len(PHBfeats))
print('PHB page range:', min(f['page'] for f in PHBfeats), max(f['page'] for f in PHBfeats))

