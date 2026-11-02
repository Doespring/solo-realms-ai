
import sys, re, json, os
sys.stdout.reconfigure(encoding='utf-8')
OUT = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru'
TEXT = OUT + r'\text'
def load(fn):
    t = open(TEXT+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
S = load('SRD521.full.txt'); P = load('PHB2024ru.full.txt'); G = load('GM2024ru.full.txt')
def pgn(T,pos): return T[:pos].count('===== [стр.')
def srd_page(term):
    m = re.search(re.escape(term), S, re.I)
    return pgn(S, m.start()) if m else None
def first_phb(ru):
    if not ru: return None
    m = re.search(re.escape(ru), P)
    return pgn(P, m.start()) if m else None
def first_gm(ru):
    if not ru: return None
    m = re.search(re.escape(ru), G)
    return pgn(G, m.start()) if m else None
source_book = 'PHB2024ru (Книга игрока 2024, пер. Ennin Bearsome и др., boosty.to/dungeons_ru)'
gm_book = 'GM2024ru (Руководство Мастера 2024, фан-пер. в.1.1 от 2026-08-27)'

rows = []
problems = []
def add(cat, en, ru, book=None, page=None, note=None, gm=None, gmnote=None, verify_en=True, verify_ru=None):
    book = book or source_book
    if page is None: page = first_phb(ru)
    if ru and verify_ru is None: verify_ru = ru
    if verify_ru and verify_ru not in P and not gm:
        problems.append(('RU_NOT_FOUND_PHB', en, ru))
    if verify_en and en and en not in S:
        problems.append(('EN_NOT_FOUND_SRD', en, ru))
    if page is None:
        problems.append(('NO_PAGE', en, ru))
    rows.append({'en_term': en, 'ru_term': ru, 'source_book': book, 'page': page,
                 'category': cat, 'en_source': 'SRD521' if (verify_en and en in S) else 'no SRD match',
                 'gm_ru': gm, 'gm_source': (gm_book if gm else None), 'gm_page': first_gm(gm) if gm else None,
                 'note': note, 'divergence': gmnote})

# ---- 1. Abilities
for en, ru, ab in [('Strength','Сила','СИЛ'),('Dexterity','Ловкость','ЛОВ'),('Constitution','Телосложение','ТЕЛ'),('Intelligence','Интеллект','ИНТ'),('Wisdom','Мудрость','МДР'),('Charisma','Харизма','ХАР')]:
    add('ability', en, ru, page=11, note=f'сокращение в блоке статистики: {ab}')
for en, ru in [('Ability Score','Значение характеристики'),('Ability Modifier','Модификатор характеристики'),('Ability Check','Проверка характеристики'),('Saving Throw','Спасбросок'),('Skill','Навык')]:
    add('ability', en, ru)
# ---- 2. Skills (18)
skills = [('Acrobatics','Акробатика','Ловкость'),('Animal Handling','Уход за животными','Мудрость'),('Arcana','Тайная магия','Интеллект'),('Athletics','Атлетика','Сила'),('Deception','Обман','Харизма'),('History','История','Интеллект'),('Insight','Проницательность','Мудрость'),('Intimidation','Запугивание','Харизма'),('Investigation','Расследование','Интеллект'),('Medicine','Медицина','Мудрость'),('Nature','Природа','Интеллект'),('Perception','Восприятие','Мудрость'),('Performance','Выступление','Харизма'),('Persuasion','Убеждение','Харизма'),('Religion','Религия','Интеллект'),('Sleight of Hand','Ловкость рук','Ловкость'),('Stealth','Скрытность','Ловкость'),('Survival','Выживание','Мудрость')]
for en, ru, ab in skills:
    gm = None; gmn = None
    if en == 'Investigation': gm, gmn = 'Анализ', 'GM2024ru весь текст: расследование = «Анализ» (проверка Интеллекта (Анализ)); «Расследование» в GM не встречается (0 совпадений)'
    if en == 'Perception': gm, gmn = 'Внимательность', 'GM2024ru весь текст: восприятие = «Внимательность» (проверка Мудрости (Внимательность), пассивная Внимательность (ПВ)); «Восприятие» встречается 1 раз (в титре)'
    add('skill', en, ru, page=15, note=f'характеристика: {ab}', gm=gm, gmnote=gmn)
# ---- 3. Conditions (15)
conds = [('Blinded','Ослеплённый'),('Charmed','Очарованный'),('Deafened','Оглохший'),('Exhaustion','Истощённый'),('Frightened','Испуганный'),('Grappled','Схваченный'),('Incapacitated','Недееспособный'),('Invisible','Невидимый'),('Paralyzed','Парализованный'),('Petrified','Окаменевший'),('Poisoned','Отравленный'),('Prone','Сбитый с ног'),('Restrained','Опутанный'),('Stunned','Ошеломлённый'),('Unconscious','Бессознательный')]
gm_cond = {'Prone': ('лежащий ничком','GM2024ru: Prone = «лежащий ничком» (состояние лежащий ничком), «Сбитый с ног» — 0 совпадений'),
           'Stunned': ('оглушённый','GM2024ru: Stunned = «оглушённый» (1 совпадение, строки блоков), PHB — «Ошеломлённый»')}
for en, ru in conds:
    g = gm_cond.get(en, (None,None))[0]; gn = gm_cond.get(en, (None,None))[1]
    add('condition', en, ru, page=None, note='метка [Состояние] в Глоссарии правил', gm=g, gmnote=gn, verify_ru=ru+' [Состояние]' if en!='Prone' else 'Сбитый с ног [Состояние]')
    rows[-1]['page'] = {'Blinded':372,'Charmed':373,'Deafened':372,'Exhaustion':368,'Frightened':368,'Grappled':379,'Incapacitated':371,'Invisible':370,'Paralyzed':373,'Petrified':372,'Poisoned':373,'Prone':377,'Restrained':372,'Stunned':373,'Unconscious':363}[en]
# ---- 4. Actions (12 core) + others
acts = [('Attack','Атака'),('Dash','Рывок'),('Disengage','Отход'),('Dodge','Уклонение'),('Help','Помощь'),('Hide','Засада'),('Influence','Влияние'),('Magic','Магия'),('Ready','Подготовка'),('Search','Поиск'),('Study','Изучение'),('Utilize','Использование')]
pages_a = {'Attack':362,'Dash':377,'Disengage':373,'Dodge':380,'Help':374,'Hide':366,'Influence':364,'Magic':370,'Ready':374,'Search':374,'Study':367,'Utilize':367}
gm_a = {'Hide':('Засада',None),'Dodge':('Уклонение',None)}
for en, ru in acts:
    add('action', en+' action', ru, page=pages_a[en], note='метка [Действие] в Глоссарии правил', verify_ru=ru)
add('action','Bonus Action','Бонусное действие')
add('action','Reaction','Реакция')
add('action','Movement','Перемещение', verify_en=False, page=23)
add('action','Opportunity Attack','Провоцированная атака')
add('action','Unarmed Strike','Безоружный удар')
# ---- 5. Combat terms
for en, ru in [('Initiative','Инициатива'),('Surprise','Застигнутый врасплох'),('Critical Hit','Критическое попадание'),('Bloodied','Окровавленный'),('Death Saving Throw','Спасбросок от смерти'),('Hit Points','Хиты'),('Hit Point Dice','Кости хитов'),('Temporary Hit Points','Временные хиты'),('Armor Class','Класс защиты (КЗ)'),('Difficulty Class','Класс сложности (Сл)'),('Advantage','Преимущество'),('Disadvantage','Помеха'),('Proficiency Bonus','Бонус владения (БВ)'),('Proficiency','Владение'),('Cover','Укрытие'),('Half Cover','Укрытие на половину'),('Three-Quarters Cover','Укрытие на три четверти'),('Total Cover','Полное укрытие'),('Difficult Terrain','Труднопроходимая местность'),('Damage','Урон'),('Damage Threshold','Порог урона'),('Resistance','Сопротивление'),('Vulnerability','Уязвимость'),('Immunity','Иммунитет'),('Concentration','Концентрация'),('Heroic Inspiration','Героическое вдохновение'),('Initiative roll','бросок Инициативы'),('Attitude','Отношение'),('Hostile','Враждебное'),('Friendly','Дружелюбное'),('Indifferent','Равнодушное'),('Short Rest','Короткий отдых'),('Long Rest','Долгий отдых'),('Alignment','Мировоззрение'),('Speed','Скорость'),('Size','Размер'),('Creature Type','Тип существа'),('Damage Type','Тип урона'),('Saving Throw DC','Сл спасброска'),('Attack Roll','Бросок атаки')]:
    gm=None; gmn=None
    if en=='Armor Class': gm, gmn = 'КД', 'GM2024ru: Armor Class = «КД» (62 совпадения в блоках статистики и тексте); «Класс защиты» — 0'
    if en=='Proficiency Bonus': gm, gmn = 'БМ', 'GM2024ru блоки статистики: «(Опыт 0; БМ +2)»; в тексте GM также «бонус владения» (4 совпадения); PHB — «БВ»'
    if en=='Passive Perception': gm, gmn = 'ПВ'
    add('combat', en, ru, gm=gm, gmnote=gmn)
add('combat','Passive Perception','Пассивное восприятие', gm='Внимательность (ПВ)', gmnote='GM: «пассивная Внимательность (ПВ)»')
add('combat','Bloodied', None, note='см. строку Bloodied выше', verify_ru=None) if False else None
# ---- 6. Senses / exploration
for en, ru in [('Darkvision','Тёмное зрение'),('Blindsight','Слепое зрение'),('Truesight','Истинное зрение'),('Telepathy','Телепатия'),('Lightly Obscured','Слабо заслонённая'),('Heavily Obscured','Сильно заслонённая'),('Bright Light','Яркий свет'),('Dim Light','Тусклый свет'),('Darkness','Тьма'),('Climbing','Лазание'),('Swimming','Плавание'),('Crawling','Ползание'),('Jumping','Прыжки'),('Falling','Падение'),('Suffocation','Удушение'),('Burning','Горение'),('Dehydration','Обезвоживание'),('Malnutrition','Недоедание'),('Travel Pace','Темп путешествия'),('Exploration','Исследование'),('Social Interaction','Социальное взаимодействие'),('Area of Effect','Область воздействия'),('Cone','Конус'),('Line','Линия'),('Cylinder','Цилиндр'),('Cube','Куб'),('Sphere','Сфера'),('Emanation','Эманация')]:
    add('exploration', en, ru)
# ---- 7. Damage types (13)
for en, ru in [('Acid','Кислота'),('Bludgeoning','Дробящий'),('Cold','Холод'),('Fire','Огонь'),('Force','Чистая сила'),('Lightning','Электричество'),('Necrotic','Некротическая энергия'),('Piercing','Колющий'),('Poison','Яд'),('Psychic','Психическая энергия'),('Radiant','Излучение'),('Slashing','Рубящий'),('Thunder','Звук')]:
    gm=None; gmn=None
    if en=='Force': gm, gmn = 'силовое поле', 'GM2024ru: «урон силовым полем» (17 совпадений); PHB: «Чистая сила»'
    add('damage_type', en, ru, page=380, note='таблица «Типы урона»', gm=gm, gmnote=gmn)
# ---- 8. Schools (8)
for en, ru in [('Abjuration','Ограждение'),('Conjuration','Призыв'),('Divination','Прорицание'),('Enchantment','Очарование'),('Evocation','Воплощение'),('Illusion','Иллюзия'),('Necromancy','Некромантия'),('Transmutation','Преобразование')]:
    add('school', en, ru, page=236, note='таблица «Школы магии»')
# ---- 9. Spell levels
for en, ru in [('Spell','Заклинание'),('Cantrip','Заговор'),('Level 1 Spell','1-й уровень заклинаний'+'') ,('Spell Slot','Ячейка заклинаний'),('Ritual','Ритуал'),('Casting Time','Время сотворения'),('Range (spell)','Дистанция'),('Components','Компоненты'),('Duration','Длительность'),('Spellcasting Ability','Заклинательная характеристика'),('School of Magic','Школа магии'),('Upcasting','Использование ячейки большего уровня'),('Spell Scroll','Свиток заклинания')]:
    if en=='Level 1 Spell':
        rows.append({'en_term':'Spell Level (0-9)','ru_term':'Уровень заклинания (от 0-го до 9-го)','source_book':source_book,'page':235,'category':'spell_level','en_source':'SRD521','gm_ru':None,'gm_source':None,'gm_page':None,'note':'0-й уровень = Заговор','divergence':None})
        continue
    add('spell_level', en, ru)
# ---- 10. Classes (12)
cls = [('Barbarian','Варвар',61),('Bard','Бард',50),('Cleric','Жрец',103),('Druid','Друид',91),('Fighter','Воин',69),('Monk','Монах',125),('Paladin','Паладин',133),('Ranger','Следопыт',153),('Rogue','Плут',143),('Sorcerer','Чародей',163),('Warlock','Колдун',113),('Wizard','Волшебник',79)]
for en, ru, pg in cls:
    add('class', en, ru, page=pg)
# ---- 11. Subclasses (48 PHB / 12 SRD)
subs = [('College of Valor','Коллегия доблести',57),('College of Lore','Коллегия знаний',58),('College of Glamour','Коллегия очарования',59),('College of Dance','Коллегия танца',60),('Path of the Berserker','Путь берсерка',65),('Path of the Wild Heart','Путь дикого сердца',66),('Path of the World Tree','Путь Мирового Древа',67),('Path of the Zealot','Путь фанатика',68),('Battle Master','Мастер боя',73),('Eldritch Knight','Мистический рыцарь',75),('Psi Warrior','Пси-воин',76),('Champion','Чемпион',78),('Evoker','Воплотитель',87),('Illusionist','Иллюзионист',88),('Abjurer','Оградитель',89),('Diviner','Прорицатель',90),('Circle of the Stars','Круг звёзд',98),('Circle of the Land','Круг земли',99),('Circle of the Moon','Круг луны',101),('Circle of the Sea','Круг моря',102),('War Domain','Домен войны',109),('Life Domain','Домен жизни',110),('Trickery Domain','Домен обмана',111),('Light Domain','Домен света',112),('Archfey Patron','Покровительство Архифеи',121),('Great Old One Patron','Покровительство Великого Древнего',122),('Fiend Patron','Покровительство Исчадия',123),('Celestial Patron','Покровительство Небожителя',124),('Warrior of Mercy','Воин милосердия',129),('Warrior of the Open Hand','Воин открытой ладони',130),('Warrior of the Elements','Воин стихий',131),('Warrior of Shadow','Воин тени',132),('Oath of Vengeance','Клятва возмездия',138),('Oath of the Ancients','Клятва древних',139),('Oath of Devotion','Клятва преданности',141),('Oath of Glory','Клятва славы',142),('Thief','Вор',147),('Soulknife','Клинок душ',149),('Arcane Trickster','Мистический ловкач',150),('Assassin','Убийца',152),('Hunter','Охотник',158),('Beast Master','Повелитель зверей',159),('Fey Wanderer','Странник фей',160),('Gloom Stalker','Сумеречный охотник',162),('Aberrant Sorcery','Аберрантное чародейство',170),('Draconic Sorcery','Драконье чародейство',172),('Clockwork Sorcery','Заводное чародейство',173),('Wild Magic Sorcery','Чародейство дикой магии',174)]
for en, ru, pg in subs:
    rows.append({'en_term':en,'ru_term':ru,'source_book':source_book,'page':pg,'category':'subclass','en_source':'SRD521 (Subclass: …)' if en in S else 'SRD521 in: SRD contains only 12 subclasses — this one is PHB-only','gm_ru':None,'gm_source':None,'gm_page':None,'note':'название в PHB дано в кавычках: «'+ru+'»','divergence':None})
# ---- 12. Species (10)
sp = [('Aasimar','Аасимар',186),('Gnome','Гном',187),('Goliath','Голиаф',188),('Dwarf','Дварф',189),('Dragonborn','Драконорождённый',190),('Orc','Орк',191),('Halfling','Полурослик',192),('Tiefling','Тифлинг',193),('Human','Человек',195),('Elf','Эльф',196)]
for en, ru, pg in sp:
    add('species', en, ru, page=pg, note='глава 4 «Описания видов» (с.186: «В этом разделе представлены десять видов»)', verify_ru='Аасимар' if en=='Aasimar' else ru)
# ---- 13. Backgrounds (16)
bgs = [('Acolyte','Прислужник'),('Artisan','Ремесленник'),('Charlatan','Шарлатан'),('Criminal','Преступник'),('Entertainer','Артист'),('Farmer','Фермер'),('Guard','Стражник'),('Guide','Проводник'),('Hermit','Отшельник'),('Merchant','Торговец'),('Noble','Благородный'),('Sage','Мудрец'),('Sailor','Моряк'),('Scribe','Писарь'),('Soldier','Солдат'),('Wayfarer','Бродяга')]
bg_pages = {'Прислужник':182,'Ремесленник':183,'Шарлатан':186,'Преступник':181,'Артист':178,'Фермер':186,'Стражник':184,'Проводник':182,'Отшельник':180,'Торговец':184,'Благородный':178,'Мудрец':180,'Моряк':179,'Писарь':181,'Солдат':183,'Бродяга':179}
for en, ru in bgs:
    add('background', en, ru, page=bg_pages[ru], note='глава 4 «Описания предысторий»; названия в двухколоночном списке')
# ---- 14. Feats (81 from PHB list) with category
feats = """Амбидекстр|Универсальные|Dual Wielder
Артистичный|Универсальные|Actor
Атлетичный|Универсальные|Athlete
Бдительный|Происхождение|Alert
Боевой заклинатель|Универсальные|War Caster
Борец|Универсальные|Grappler
Быстрый|Универсальные|Speedy
Везунчик|Происхождение|Lucky
Верховой боец|Универсальные|Mounted Combatant
Внимательный|Универсальные|Observant
Воинская подготовка|Универсальные|Martial Weapon Training
Воодушевляющий лидер|Универсальные|Inspiring Leader
Дар боевой удали|Эпический дар|Boon of Combat Prowess
Дар восстановления|Эпический дар|Boon of Recovery
Дар восстановления заклинаний|Эпический дар|Boon of Spell Recall
Дар истинного зрения|Эпический дар|Boon of Truesight
Дар межпространственного перемещения|Эпический дар|Boon of Dimensional Travel
Дар неотразимого нападения|Эпический дар|Boon of Irresistible Offense
Дар ночного духа|Эпический дар|Boon of the Night Spirit
Дар скорости|Эпический дар|Boon of Speed
Дар сопротивления энергии|Эпический дар|Boon of Energy Resistance
Дар стойкости|Эпический дар|Boon of Fortitude
Дар судьбы|Эпический дар|Boon of Fate
Дар умелости|Эпический дар|Boon of Skill
Дебошир|Происхождение|Tavern Brawler
Дикий атакующий|Происхождение|Savage Attacker
Дуэлянт|Боевой стиль|Dueling
Затронутый тенью|Универсальные|Shadow-Touched
Затронутый феями|Универсальные|Fey-Touched
Защита|Боевой стиль|Defense
Знаток лёгких доспехов|Универсальные|Light Armor Training
Знаток средних доспехов|Универсальные|Medium Armor Training
Знаток тяжёлых доспехов|Универсальные|Heavy Armor Training
Крепкий|Происхождение|Tough
Крушитель|Универсальные|Crusher
Лекарь|Происхождение|Healer
Мастер большого оружия|Универсальные|Great Weapon Master
Мастер древкового оружия|Универсальные|Polearm Master
Мастер оружия|Универсальные|Weapon Master
Мастер средних доспехов|Универсальные|Medium Armor Master
Мастер тяжёлых доспехов|Универсальные|Heavy Armor Master
Мастер щитов|Универсальные|Shield Master
Меткий заклинатель|Универсальные|Spell Sniper
Меткий стрелок|Универсальные|Sharpshooter
Музыкант|Происхождение|Musician
Налётчик|Универсальные|Charger
Оборона|Боевой стиль|Defense
Обороняющийся дуэлянт|Универсальные|Defensive Duelist
Одарённый|Происхождение|Skilled
Острый ум|Универсальные|Keen Mind
Отравитель|Универсальные|Poisoner
Перехват|Боевой стиль|Interception
Посвящённый в магию|Происхождение|Magic Initiate
Пронзатель|Универсальные|Piercer
Проныра|Универсальные|Skulker
Ритуальный заклинатель|Универсальные|Ritual Caster
Рубака|Универсальные|Slasher
Самоделкин|Происхождение|Crafter
Сражение без оружия|Боевой стиль|Unarmed Fighting
Сражение большим оружием|Боевой стиль|Great Weapon Fighting
Сражение вслепую|Боевой стиль|Blind Fighting
Сражение двумя оружиями|Боевой стиль|Two-Weapon Fighting
Сражение метательным оружием|Боевой стиль|Thrown Weapon Fighting
Стихийный адепт|Универсальные|Elemental Adept
Стойкий|Универсальные|Resilient
Страж|Универсальные|Sentinel
Стрельба|Боевой стиль|Archery
Телекинетик|Универсальные|Telekinetic
Телепат|Универсальные|Telepathic
Убийца магов|Универсальные|Mage Slayer
Увеличение характеристик|Универсальные|Ability Score Improvement
Устойчивый|Универсальные|Tough (PHB cat.) — уточнить
Шеф-повар|Универсальные|Chef
Эксперт в арбалетах|Универсальные|Crossbow Expert
Эксперт в навыке|Универсальные|Skill Expert"""
feat_unverified = []
for line in feats.strip().split('\n'):
    ru, cat, en = line.split('|')
    en_clean = en.split(' (')[0]
    if en_clean not in S: feat_unverified.append((ru,en_clean))
    rows.append({'en_term':en_clean,'ru_term':ru,'source_book':source_book,'page':None,'category':'feat','en_source':'SRD521' if en_clean in S else 'НЕ найдено в SRD521 — перевод названия на английский не подтверждён','gm_ru':None,'gm_source':None,'gm_page':None,'note':'категория в PHB: «'+cat+'»','divergence':None})
for r in rows:
    if r['category']=='feat' and r['page'] is None:
        r['page'] = 200
# ---- 15. Weapon properties (10) + mastery (8)
wp = [('Ammunition','Боеприпас'),('Finesse','Фехтовальное'),('Heavy','Тяжёлое'),('Light','Лёгкое'),('Loading','Перезарядка'),('Range','Дистанция'),('Reach','Досягаемость'),('Thrown','Метательное'),('Two-Handed','Двуручное'),('Versatile','Универсальное')]
for en, ru in wp:
    add('weapon_property', en, ru, page=213, note='раздел «Свойства», глава 6')
wp_gm = {'Finesse':('Искусное','GM использует «Искусное владение оружием» и «Фехтовальное» не как oружие-свойство? В PHB — «Фехтовальное»')}
for en, ru in [('Cleave','Рассекающее'),('Graze','Задевающее'),('Nick','Быстрое'),('Push','Отталкивающее'),('Sap','Ослабляющее'),('Slow','Замедляющее'),('Topple','Опрокидывающее'),('Vex','Отвлекающее')]:
    add('mastery_property', en, ru, page=214, note='раздел «Свойства искусности» (Weapon Mastery)')
add('mastery_property','Weapon Mastery','Искусство владения оружием', page=214, note='PHB: заголовок «Свойства искусности»; умение класса называется «Искусство владения оружием»', verify_ru='Свойства искусности')
# ---- 16. Weapon categories & weapons
for en, ru in [('Simple Weapon','Простое оружие'),('Martial Weapon','Воинское оружие'),('Melee Weapon','Рукопашное оружие'),('Ranged Weapon','Дальнобойное оружие'),('Improvised Weapon','Импровизированное оружие')]:
    add('weapon_category', en, ru, page=213)
weapons = [('Club','Дубинка'),('Dagger','Кинжал'),('Greatclub','Палица'),('Handaxe','Одноручный топор'),('Javelin','Метательное копьё'),('Light Hammer','Лёгкий молот'),('Mace','Булава'),('Quarterstaff','Боевой посох'),('Sickle','Серп'),('Spear','Копьё'),('Dart','Дротик'),('Light Crossbow','Лёгкий арбалет'),('Shortbow','Короткий лук'),('Sling','Праща'),('Battleaxe','Боевой топор'),('Flail','Цеп'),('Glaive','Глефа'),('Greataxe','Секира'),('Greatsword','Двуручный меч'),('Halberd','Алебарда'),('Lance','Кавалерийское копьё'),('Longsword','Длинный меч'),('Maul','Молот'),('Morningstar','Моргенштерн'),('Pike','Пика'),('Rapier','Рапира'),('Scimitar','Скимитар'),('Shortsword','Короткий меч'),('Trident','Трезубец'),('Warhammer','Боевой молот'),('War Pick','Клевец'),('Whip','Кнут'),('Blowgun','Духовая трубка'),('Hand Crossbow','Одноручный арбалет'),('Heavy Crossbow','Тяжёлый арбалет'),('Longbow','Длинный лук'),('Musket','Мушкет'),('Pistol','Пистоль')]
for en, ru in weapons:
    add('weapon', en, ru, page=216)
# ---- 17. Armor
for en, ru in [('Light Armor','Лёгкий доспех'),('Medium Armor','Средний доспех'),('Heavy Armor','Тяжёлый доспех'),('Shield','Щит'),('Armor Training','Навык обращения с доспехами')]:
    add('armor_category', en, ru, page=219)
armors = [('Padded Armor','Стёганный доспех'),('Leather Armor','Кожаный доспех'),('Studded Leather Armor','Проклёпанный кожаный доспех'),('Hide Armor','Шкурный доспех'),('Chain Shirt','Кольчужная рубаха'),('Scale Mail','Чешуйчатый доспех'),('Breastplate','Кираса'),('Half Plate Armor','Полулаты'),('Ring Mail','Колечный доспех'),('Chain Mail','Кольчуга'),('Splint Armor','Наборный доспех'),('Plate Armor','Латы')]
for en, ru in armors:
    add('armor', en, ru, page=219)
# ---- 18. Creature types (14)
ct = [('Aberration','Аберрация'),('Beast','Зверь'),('Celestial','Небожитель'),('Construct','Конструкт'),('Dragon','Дракон'),('Elemental','Элементаль'),('Fey','Фея'),('Fiend','Исчадие'),('Giant','Великан'),('Humanoid','Гуманоид'),('Monstrosity','Монстр'),('Ooze','Слизь'),('Plant','Растение'),('Undead','Нежить')]
for en, ru in ct:
    add('creature_type', en, ru, page=380, note='список типов существ в статье «Тип существа»')
# ---- 19. Sizes (6)
for en, ru in [('Tiny','Крошечный'),('Small','Маленький'),('Medium','Средний'),('Large','Большой'),('Huge','Огромный'),('Gargantuan','Громадный')]:
    add('size', en, ru, page=None, note='статья «Размер» глоссария')
for r in rows:
    if r['category']=='size' and r['ru_term']=='Крошечный': r['page']=376
    if r['category']=='size' and r['ru_term'] in ('Огромный','Громадный','Большой','Средний','Маленький'): r['page']=376
# ---- 20. Alignment (9)
al = [('Lawful Good','Принципиально-добрый (ПД)'),('Neutral Good','Нейтрально-добрый (НД)'),('Chaotic Good','Хаотично-добрый (ХД)'),('Lawful Neutral','Принципиально-нейтральный (ПН)'),('Neutral','Нейтральный (Н)'),('Chaotic Neutral','Хаотично-нейтральный (ХН)'),('Lawful Evil','Принципиально-злой (ПЗ)'),('Neutral Evil','Нейтрально-злой (НЗ)'),('Chaotic Evil','Хаотично-злой (ХЗ)')]
gm_al = {'Lawful Good':'законопослушный добрый (ЗД)','Lawful Evil':'законопослушный злой (ЗЗ)','Neutral Good':'нейтральный добрый','Chaotic Evil':'хаотичный злой (ХЗ)','Chaotic Good':'хаотичный добрый','Lawful Neutral':'законный нейтральный','Neutral':'нейтральный'}
for en, ru in al:
    add('alignment', en, ru, page=362, note='сокращения ПД/НД/ХД/ПН/Н/ХН/ПЗ/НЗ/ХЗ из списка сокращений глоссария; полные названия — на таблицах мировоззрения',
        gm=gm_al.get(en), gmnote=(gm_al.get(en) and 'GM2024ru «Мировоззрение»: «законопослушный добрый (ЗД)», «законопослушный злой (ЗЗ)», «хаотичный злой (ХЗ)» — иные основы, чем в PHB «Принципиально-добрый (ПД)»'))
# ---- 21. Languages
langs = [('Common','Общий'),('Common Sign Language','Общий жестовый'),('Draconic','Драконий'),('Dwarvish','Дварфийский'),('Elvish','Эльфийский'),('Giant','Великаний'),('Gnomish','Гномий'),('Goblin','Гоблинский'),('Halfling','Язык полуросликов'),('Orc','Орочий'),('Abyssal','Язык Бездны'),('Celestial','Небесный'),('Deep Speech','Глубинная речь'),('Druidic','Друидический'),('Infernal','Инфернальный'),('Primordial','Первичный'),('Sylvan','Сильван'),('Undercommon','Подземный'),('Thieves’ Cant','Воровской жаргон')]
for en, ru in langs:
    add('language', en, ru, page=37, note='таблицы «Обычные языки» и «Редкие языки»')

json.dump(rows, open(OUT+r'\_rows_raw.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
json.dump(problems, open(OUT+r'\_problems.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('rows', len(rows), 'problems', len(problems), 'feat_unverified', len(feat_unverified))

