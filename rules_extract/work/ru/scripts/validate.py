
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
OUT = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru'
rep = []
# 1 validate JSONL
for fn in ('RU_GLOSSARY.jsonl','RU_UI_STRINGS.jsonl'):
    n=0; bad=[]
    for ln,line in enumerate(open(OUT+'\\'+fn, encoding='utf-8'),1):
        line=line.strip()
        if not line: continue
        try: json.loads(line); n+=1
        except Exception as e: bad.append((ln,str(e)))
    rep.append(f'{fn}: {n} строк, ошибок JSON: {len(bad)}')
gl = [json.loads(l) for l in open(OUT+r'\RU_GLOSSARY.jsonl',encoding='utf-8') if l.strip()]
# 2 coverage checks
def cov(cat, expected):
    got = [r for r in gl if r['category']==cat]
    missing = [e for e in expected if not any(g['en_term']==e for g in got)]
    return len(got), missing
skills_en = ['Acrobatics','Animal Handling','Arcana','Athletics','Deception','History','Insight','Intimidation','Investigation','Medicine','Nature','Perception','Performance','Persuasion','Religion','Sleight of Hand','Stealth','Survival']
conds_en = ['Blinded','Charmed','Deafened','Exhaustion','Frightened','Grappled','Incapacitated','Invisible','Paralyzed','Petrified','Poisoned','Prone','Restrained','Stunned','Unconscious']
dmg_en = ['Acid','Bludgeoning','Cold','Fire','Force','Lightning','Necrotic','Piercing','Poison','Psychic','Radiant','Slashing','Thunder']
school_en = ['Abjuration','Conjuration','Divination','Enchantment','Evocation','Illusion','Necromancy','Transmutation']
acts_en = ['Attack action','Dash action','Disengage action','Dodge action','Help action','Hide action','Influence action','Magic action','Ready action','Search action','Study action','Utilize action']
for name,cat,exp in [('навыки','skill',skills_en),('состояния','condition',conds_en),('типы урона','damage_type',dmg_en),('школы магии','school',school_en),('действия','action',acts_en)]:
    n,miss = cov(cat,exp)
    rep.append(f'{name}: записей {n}, ожидалось {len(exp)}, не найдено: {miss}')
# 3 duplicates
from collections import Counter
dup=[k for k,v in Counter((r['category'],r['en_term'],r['ru_term']) for r in gl).items() if v>1]
rep.append(f'дубликаты (category,en,ru): {len(dup)} {dup[:10]}')
# 4 no-page / unverified
nopage=[(r['en_term'],r['ru_term']) for r in gl if r['page'] is None]
rep.append(f'без page: {len(nopage)} {nopage}')
uns=[(r['ru_term'],r['en_term']) for r in gl if r['en_source'].startswith('нет в SRD')]
rep.append(f'EN не подтверждён SRD: {len(uns)} (feats etc.)')
# 5 page range sanity
badpage=[(r['en_term'],r['page']) for r in gl if r['page'] is not None and not (1<=r['page']<=388)]
rep.append(f'page вне 1..388: {len(badpage)} {badpage[:5]}')
# 6 divergences
div=[(r['en_term'],r['ru_term'],r['gm_ru']) for r in gl if r.get('gm_ru')]
rep.append(f'строк с GM-вариантом: {len(div)}')
for d in div: rep.append('   DIV: '+ ' | '.join(str(x) for x in d))
ui=[json.loads(l) for l in open(OUT+r'\RU_UI_STRINGS.jsonl',encoding='utf-8') if l.strip()]
rep.append(f'UI строк: {len(ui)}; с GM-вариантом: {sum(1 for u in ui if u.get("ru_gm"))}')
open(OUT+r'\_validation.txt','w',encoding='utf-8').write('\n'.join(rep))
print('\n'.join(rep))

