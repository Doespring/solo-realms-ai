
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
OUT = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru'
TEXT = OUT + r'\text'
def load(fn):
    t = open(TEXT+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
S = load('SRD521.full.txt'); P = load('PHB2024ru.full.txt')
def pgn(T,pos): return T[:pos].count('===== [стр.')
rows = json.load(open(OUT+r'\_rows_raw.json', encoding='utf-8'))
# fix subclass en_source flag
for r in rows:
    if r['category']=='subclass':
        r['en_source'] = 'SRD521 (Subclass)' if r['en_term'] in S else 'нет в SRD 5.2.1 (в SRD только 12 подклассов) — англ. название по PHB 2024'
for r in rows:
    if r['en_term']=='Spell Slot':
        r['page'] = pgn(P, P.find('Ячейки заклинаний'))
CATMAP = {
 'ability':'характеристики','skill':'навыки','condition':'состояния','action':'действия',
 'combat':'боевые термины','exploration':'исследование/взаимодействие/местность','damage_type':'типы урона',
 'school':'школы магии','spell_level':'уровни/компоненты заклинаний','class':'классы','subclass':'подклассы',
 'species':'виды','background':'предыстории','feat':'черты','weapon_property':'свойства оружия',
 'mastery_property':'свойства искусности (Weapon Mastery)','weapon_category':'категории оружия','weapon':'виды оружия',
 'armor_category':'категории доспехов/владение доспехами','armor':'виды доспехов','creature_type':'типы существ',
 'size':'размеры','alignment':'мировоззрение','language':'языки'}
with open(OUT+r'\RU_GLOSSARY.jsonl','w',encoding='utf-8',newline='\n') as f:
    n=0
    for r in rows:
        out = {
          'en_term': r['en_term'],
          'ru_term': r['ru_term'],
          'source_book': r['source_book'],
          'page': r['page'],
          'category': r['category'],
          'category_ru': CATMAP.get(r['category'], r['category']),
          'en_source': r['en_source'],
          'gm_ru': r['gm_ru'],
          'gm_source': r['gm_source'],
          'gm_page': r['gm_page'],
          'note': r['note'],
          'divergence': r['divergence'],
        }
        f.write(json.dumps(out, ensure_ascii=False)+'\n'); n+=1
print('wrote', n)
# stats
from collections import Counter
c = Counter(r['category'] for r in rows)
print(json.dumps(c, ensure_ascii=False))
print('divergences', sum(1 for r in rows if r['gm_ru']))

