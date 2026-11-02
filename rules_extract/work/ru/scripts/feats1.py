
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
OUT = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru'
TEXT = OUT + r'\text'
def load(fn):
    t = open(TEXT+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
S = load('SRD521.full.txt'); P = load('PHB2024ru.full.txt')
mapping = {
'Амбидекстр':'Dual Wielder','Артистичный':'Actor','Атлетичный':'Athlete','Бдительный':'Alert','Боевой заклинатель':'War Caster','Борец':'Grappler','Быстрый':'Speedy','Везунчик':'Lucky','Верховой боец':'Mounted Combatant','Внимательный':'Observant','Воинская подготовка':'Martial Weapon Training','Воодушевляющий лидер':'Inspiring Leader',
'Дар боевой удали':'Boon of Combat Prowess','Дар восстановления':'Boon of Recovery','Дар восстановления заклинаний':'Boon of Spell Recall','Дар истинного зрения':'Boon of Truesight','Дар межпространственного перемещения':'Boon of Dimensional Travel','Дар неотразимого нападения':'Boon of Irresistible Offense','Дар ночного духа':'Boon of the Night Spirit','Дар скорости':'Boon of Speed','Дар сопротивления энергии':'Boon of Energy Resistance','Дар стойкости':'Boon of Fortitude','Дар судьбы':'Boon of Fate','Дар умелости':'Boon of Skill',
'Дебошир':'Tavern Brawler','Дикий атакующий':'Savage Attacker','Дуэлянт':'Dueling','Затронутый тенью':'Shadow-Touched','Затронутый феями':'Fey-Touched','Защита':'Defense (см. Оборона)','Знаток лёгких доспехов':'Light Armor Training','Знаток средних доспехов':'Medium Armor Training','Знаток тяжёлых доспехов':'Heavy Armor Training','Крепкий':'Tough','Крушитель':'Crusher','Лекарь':'Healer','Мастер большого оружия':'Great Weapon Master','Мастер древкового оружия':'Polearm Master','Мастер оружия':'Weapon Master','Мастер средних доспехов':'Medium Armor Master','Мастер тяжёлых доспехов':'Heavy Armor Master','Мастер щитов':'Shield Master','Меткий заклинатель':'Spell Sniper','Меткий стрелок':'Sharpshooter','Музыкант':'Musician','Налётчик':'Charger','Оборона':'Defense','Обороняющийся дуэлянт':'Defensive Duelist','Одарённый':'Skilled','Острый ум':'Keen Mind','Отравитель':'Poisoner','Перехват':'Interception','Посвящённый в магию':'Magic Initiate','Пронзатель':'Piercer','Проныра':'Skulker','Ритуальный заклинатель':'Ritual Caster','Рубака':'Slasher','Самоделкин':'Crafter','Сражение без оружия':'Unarmed Fighting','Сражение вслепую':'Blind Fighting','Сражение двумя оружиями':'Two-Weapon Fighting','Стихийный адепт':'Elemental Adept','Стойкий':'Resilient','Страж':'Sentinel (см. «Страж» = Sentinel)','Стрельба':'Archery','Телекинетик':'Telekinetic','Телепат':'Telepathic','Убийца магов':'Mage Slayer','Увеличение характеристик':'Ability Score Improvement','Устойчивый':'Undetermined — в SRD 2024 нет отдельной черты с этим именем; вероятно PHB-специфичная','Шеф-повар':'Chef','Эксперт в арбалетах':'Crossbow Expert','Эксперт в навыке':'Skill Expert'}
PHB = load('PHB2024ru.full.txt')
def page_of(ru):
    # page where the feat's own description heading appears (after the list table)
    i = PHB.find('Черты происхождения\nЭти черты')
    j = PHB.find(ru+'\n', i)
    if j < 0: j = PHB.find(ru, 0)
    return PHB[:j].count('===== [стр.')
feat_rows=[]
for ru, cat in json.load(open(OUT+r'\_feat_ru.json',encoding='utf-8')):
    en = mapping.get(ru)
    if ru == 'Оборона': en = 'Defense'; note='в «Списке черт» фигурируют и «Защита», и «Оборона» с одной категорией Боевой стиль — вероятная колонтитульная ошибка/дубль; в SRD 5.2.1 Fighting Style feat = Defense'
    else: note='категория в PHB: «'+cat+'»'
    pg = page_of(ru)
    feat_rows.append({'en_term':en,'ru_term':ru,'source_book':'PHB2024ru','page':pg,'category':'feat','en_source':'SRD521' if (en and en in S) else 'англ. название НЕ подтверждено в SRD 5.2.1 — требует сверки с PHB 2024 EN','note':note,'cat_phb':cat})
missing=[f for f in feat_rows if not f['en_term'] or f['en_term'] not in S]
print(len(feat_rows), 'unconfirmed:', [(f['ru_term'],f['en_term']) for f in missing])
json.dump(feat_rows, open(OUT+r'\_feat_rows.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)

