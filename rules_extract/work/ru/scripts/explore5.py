
import re, sys
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load_pages(name):
    txt = open(base+'\\'+name+'.full.txt', encoding='utf-8').read()
    parts = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', txt)
    return {int(parts[i]): parts[i+1] for i in range(1, len(parts), 2)}
pg = load_pages('SRD521')
# find index of headings by scanning lines that look like headings (short, no trailing punctuation)
for p in range(5, 60):
    t = pg.get(p,'').replace('\r\n','\n')
    lines = [l.strip() for l in t.split('\n')]
    heads = [l for l in lines if l and len(l) < 40 and l[0].isupper() and not l.endswith(('.',',',':')) and not re.search(r'\d{2,}$', l)]
    if heads: print(p, '|', ' ;; '.join(heads[:12]))

