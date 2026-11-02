
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
OUT = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru'
TEXT = OUT + r'\text'
def load(fn):
    t = open(TEXT+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
S = load('SRD521.full.txt'); P = load('PHB2024ru.full.txt')
rows = json.load(open(OUT+r'\_rows_raw.json', encoding='utf-8'))
def find_rows(en_term, cat=None):
    return [r for r in rows if r['en_term']==en_term and (cat is None or r['category']==cat)]
# 1. Proficiency Bonus page
i = P.find('Бонус владения')
for r in find_rows('Proficiency Bonus'): r['page'] = P[:i].count('===== [стр.')
# 2. Saving Throw DC
for r in find_rows('Saving Throw DC'):
    r['en_term'] = 'DC of a saving throw (Saving Throw DC)'
    r['en_source'] = 'SRD521 (формулировка «сложность спасброска» относится к глаголу); в PHB — «Сл спасброска»'
    r['page'] = 13
# 3. Travel Pace / Spell Slot / Range / Upcasting
for r in find_rows('Travel Pace'):
    r['ru_term'] = 'нет в источнике (PHB узуализует «темп путешествия» / «скорость» в главе 1 и GM гл.2)'
# 4. Aasimar + backgrounds not in SRD
for en in ['Aasimar','Charlatan','Farmer','Hermit','Merchant','Sailor','Wayfarer']:
    for r in find_rows(en):
        r['en_source'] = 'нет в SRD 5.2.1 (виды/предыстории в SRD не включены) — англ. название по PHB 2024'
# 5. Simple/Martial weapon
for r in find_rows('Simple Weapon'):
    r['en_term'] = 'Simple weapons (category)'; r['en_source']='SRD521 («Simple and Martial weapons»)'
for r in find_rows('Martial Weapon'):
    r['en_term'] = 'Martial weapons (category)'; r['en_source']='SRD521 («Simple and Martial weapons»)'
# 6. Alignment fixes
al_fix = {'Lawful Good':('Принципиально-добрый (ПД)',40),'Neutral Good':('Нейтрально-добрый (НД)',40),'Chaotic Good':('Хаотично-добрый (ХД)',40),'Lawful Neutral':('Принципиально-нейтральный (ПН)',40),'Neutral':('Нейтральный (Н)',40),'Chaotic Neutral':('Хаотично-нейтральный (ХН)',40),'Lawful Evil':('Принципиально-злой (ПЗ)',40),'Neutral Evil':('Нейтрально-злой (НЗ)',40),'Chaotic Evil':('Хаотично-злой (ХЗ)',40)}
for en,(ru,pg) in al_fix.items():
    for r in find_rows(en):
        r['ru_term']=ru; r['page']=pg
        r['note']='полные названия в главе 2 (с.38-40); сокращения из списка сокращений глоссария (с.362)'
# 7. passive perception row duplicate rename
for r in rows:
    if r['en_term']=='Passive Perception' and r['category']=='combat' and r['ru_term']=='Пассивное восприятие':
        r['ru_term']='Пассивное восприятие (в GM: «пассивная Внимательность (ПВ)» / «ПВ»)'
# 8. Aasimar verify
for r in find_rows('Aasimar'): r['note']='глава 4 «Описания видов», с.186: «В этом разделе представлены десять видов»'
json.dump(rows, open(OUT+r'\_rows_raw.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
# verify all en terms present where claimed
bad=[r for r in rows if r['en_source'].startswith('SRD521') and r['en_term'].split(' (')[0] not in S]
print('rows', len(rows), 'srd-claim-missing', len(bad), [b['en_term'] for b in bad][:20])
print('no page', [(r['en_term'],r['ru_term']) for r in rows if r['page'] is None][:20])

