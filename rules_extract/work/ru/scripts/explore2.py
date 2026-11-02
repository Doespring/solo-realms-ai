
import re, sys
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load_pages(name):
    txt = open(base+'\\'+name+'.full.txt', encoding='utf-8').read()
    parts = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', txt)
    return {int(parts[i]): parts[i+1] for i in range(1, len(parts), 2)}

def dfind(name, pat):
    pg = load_pages(name)
    rx = re.compile(pat)
    res = []
    for p,t in sorted(pg.items()):
        for line in t.replace('\r\n','\n').split('\n'):
            if rx.search(line):
                res.append((p, line.strip()[:160]))
    return res

for pat in [r'Проверк[аи]\s+характеристик', r'Сложность', r'Инициатив', r'Состояни', r'урон|Урон', r'Оружие|оружия', r'Свойства мастерства|мастерств']:
    r = dfind('SRD521', pat)
    print('===', pat, 'lines:', len(r))
    for x in r[:6]: print('  ', x)

