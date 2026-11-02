
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    txt = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt); return txt
P = load('PHB2024ru.full.txt')
i = P.find('Размерство')
for kw in ['Орк','Дварф','Эльф','Тифлинг','Голиаф','Полурослик','Гном','Драконорождённый','Ассимар','Гуманоид']:
    j = P.find(kw)
    print(kw, j)
# species chapter pages
pages = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', P)
pg = {int(pages[k]): pages[k+1] for k in range(1,len(pages),2)}
for p in range(180, 250):
    t = pg.get(p,'').strip('\n')
    ls=[l for l in t.split('\n') if l.strip()]
    if ls and re.fullmatch(r'[А-ЯЁ][а-яё\-]+[ыа]?', ls[-1].strip()) and len(ls)<=3:
        print('DIV', p, ls[-1])

