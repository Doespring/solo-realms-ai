
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    txt = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt); return txt
P = load('PHB2024ru.full.txt')
print('== классы: строки со словом "Класс:" ')
for m in list(re.finditer(r'\n([А-ЯЁ][а-яё]{3,14})\n\nСвиреп|', P))[:0]: pass
# Find each class page: "===== [стр.N] =====\n<tagline>\n<ClassName>"
pages = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', P)
pg = {int(pages[i]): pages[i+1] for i in range(1,len(pages),2)}
for p in range(60, 250):
    t = pg.get(p,'').strip('\n')
    lines=[l for l in t.split('\n') if l.strip()]
    if not lines: continue
    # class title line: last line is a single capital word and page short
    if len(lines)>=2 and re.fullmatch(r'[А-ЯЁ][а-яё]+', lines[-1].strip()) and len(lines)<=3:
        print('CLASS PAGE', p, lines[-1])
    for l in lines[-2:]:
        m = re.match(r'Подкласс\s*«([^»]+)»', l.strip())
        if m: print('SUB', p, m.group(1))

