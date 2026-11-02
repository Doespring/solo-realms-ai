
import re, json, sys, io
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
import os
pages = {}
def load_pages(name):
    # jsonl exists only in original location; rebuild from full.txt
    txt = open(os.path.join(base, name+'.full.txt'), encoding='utf-8').read()
    parts = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', txt)
    out = {}
    for i in range(1, len(parts), 2):
        out[int(parts[i])] = parts[i+1]
    return out
for nm in ['PHB2024ru','GM2024ru']:
    pg = load_pages(nm)
    print(nm, 'pages:', len(pg), 'max', max(pg))
    globals()['PG_'+nm] = pg
pg = PG_PHB2024ru
# print table of contents region: first 15 pages condensed
for p in range(1, 16):
    t = pg.get(p,'')[:300].replace('\n',' | ')
    print(p, t[:280])

