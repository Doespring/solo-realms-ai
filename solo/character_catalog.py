"""Reviewed level-one PC choices. Separate editions; no runtime book scraping.

Numerical adaptations of CC BY 4.0 SRD 5.1 and 5.2.1. Attribution is shipped
in docs/SRD_ATTRIBUTION.md. Page numbers below are physical PDF pages.
Unimplemented features are records, never executable rules inferred from names.
"""
import copy
from .rulesets import SKILLS,SKILL_NAMES

ABILITIES=('strength','dexterity','constitution','intelligence','wisdom','charisma')
ABILITY_NAMES=dict(zip(ABILITIES,('Сила','Ловкость','Телосложение','Интеллект','Мудрость','Харизма')))
STANDARD=[15,14,13,12,10,8]
POINT_COST={8:0,9:1,10:2,11:3,12:4,13:5,14:7,15:9}
LANGUAGES={'common':'Общий','dwarvish':'Дварфский','elvish':'Эльфийский','giant':'Великаний','gnomish':'Гномий','goblin':'Гоблинский','halfling':'Полуросликов','orc':'Орочий','draconic':'Драконий','infernal':'Инфернальный','celestial':'Небесный','sylvan':'Сильван','undercommon':'Подземный общий','primordial':'Первичный','abyssal':'Бездны','deep_speech':'Глубинная речь','common_sign':'Общий жестовый'}
STANDARD_2024=['common_sign','draconic','dwarvish','elvish','giant','gnomish','goblin','halfling','orc']

SOURCES={
 'dnd2014':dict(title='SRD 5.1',url='https://media.wizards.com/2023/downloads/dnd/SRD_CC_v5.1.pdf',license='CC-BY-4.0'),
 'dnd2024':dict(title='SRD 5.2.1',url='https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf',license='CC-BY-4.0')}

def choice(name,**values):return dict(name=name,**values)

BASE_CLASSES={
 'fighter':choice('Воин',hit_die=10,saves=['strength','constitution'],skills='acrobatics animal_handling athletics history insight intimidation perception survival'.split(),skill_count=2,armor=['light','medium','heavy','shields'],weapons=['simple','martial'],specific_weapons=[],subclass_level=3,subclasses=['champion'],primary=['strength','dexterity'],features=['fighting_style','second_wind'],page=24,page2024=47),
 'rogue':choice('Плут',hit_die=8,saves=['dexterity','intelligence'],skills='acrobatics athletics deception insight intimidation investigation perception performance persuasion sleight_of_hand stealth'.split(),skill_count=4,armor=['light'],weapons=['simple'],specific_weapons=['hand_crossbow','longsword','rapier','shortsword'],subclass_level=3,subclasses=['thief'],primary=['dexterity'],features=['expertise','sneak_attack','thieves_cant'],page=39,page2024=61),
 'wizard':choice('Волшебник',hit_die=6,saves=['intelligence','wisdom'],skills='arcana history insight investigation medicine religion'.split(),skill_count=2,armor=[],weapons=[],specific_weapons=['dagger','dart','sling','quarterstaff','light_crossbow'],subclass_level=2,subclasses=['evocation'],primary=['intelligence'],features=['spellcasting','arcane_recovery'],page=52,page2024=77),
 'cleric':choice('Жрец',hit_die=8,saves=['wisdom','charisma'],skills='history insight medicine persuasion religion'.split(),skill_count=2,armor=['light','medium','shields'],weapons=['simple'],specific_weapons=[],subclass_level=1,subclasses=['life'],primary=['wisdom'],features=['spellcasting','disciple_of_life'],page=15,page2024=36),
}

FEATURES={
 'fighting_style':choice('Боевой стиль',status='partial',description='Выбранный стиль сохраняется; защита учитывается в AC. Остальные эффекты пока не автоматизированы.'),
 'second_wind':choice('Второе дыхание',status='recorded',description='Ресурс рассчитывается по редакции. Автоматическое лечение этой способностью ещё не подключено.'),
 'expertise':choice('Компетентность',status='implemented',description='Удваивает бонус владения для двух выбранных навыков.'),
 'sneak_attack':choice('Скрытая атака',status='recorded',description='1d6 на первом уровне; условия и дополнительный урон пока не автоматизированы.'),
 'thieves_cant':choice('Воровской жаргон',status='recorded',description='Язык сохраняется в листе; скрытые сведения NPC не выдаются автоматически.'),
 'spellcasting':choice('Использование заклинаний',status='recorded',description='Выбор заклинаний и число ячеек проверяются. Произнесение названия не выполняет заклинание: механический движок магии ещё не подключён.'),
 'arcane_recovery':choice('Магическое восстановление',status='recorded',description='Восстановление ячеек этой способностью ещё не автоматизировано.'),
 'disciple_of_life':choice('Поборник жизни',status='recorded',description='Бонус лечения домена жизни пока не автоматизирован.'),
 'ritual_adept':choice('Ритуальная магия',status='recorded',description='Ритуалы из книги заклинаний не выполняются автоматически.'),
 'weapon_mastery':choice('Мастерство оружия',status='recorded',description='Выбор видов оружия сохраняется; эффекты мастерства пока не автоматизированы.'),
 'divine_order':choice('Божественный орден',status='partial',description='Владения защитника и дополнительный заговор чудотворца рассчитываются. Бонус чудотворца к проверкам пока не автоматизирован.'),
 'human_resourceful':choice('Находчивость',status='recorded',description='Вдохновение героя после продолжительного отдыха пока не автоматизировано.'),
 # Human 2024 prints these as three separate features; the sheet shows each by name.
 'resourceful':choice('Находчивость',status='recorded',description='Вдохновение героя после продолжительного отдыха пока не автоматизировано.'),
 'skillful':choice('Умелый',status='recorded',description='Владение дополнительным навыком из выбранного списка.'),
 'versatile':choice('Многосторонний',status='recorded',description='Черта происхождения на первом уровне; выбор проверяется при сборке героя.'),
 'dwarven_toughness':choice('Дварфская стойкость',status='implemented',description='+1 к максимуму HP на первом уровне.'),
 'dwarven_resilience':choice('Дварфская устойчивость',status='recorded',description='Устойчивость к яду и связанные спасброски пока не автоматизированы.'),
 'darkvision':choice('Тёмное зрение',status='recorded',description='Дальность записана в происхождении; зрение в бою требует отдельного подключения.'),
 'stonecunning':choice('Знание камня',status='recorded',description='Эффект зависит от редакции; автоматического чувства вибрации или проверки истории нет.'),
 'fey_ancestry':choice('Наследие фей',status='recorded',description='Защита от очарования и магического сна пока не автоматизирована.'),
 'shelter_faithful':choice('Приют верующих',status='recorded',description='Помощь зависит от подтверждённого храма и отношения его служителей; автоматического исцеления нет.'),
 'alert':choice('Бдительный',status='recorded',description='Бонус и обмен инициативой пока не автоматизированы.'),
 'savage_attacker':choice('Дикий атакующий',status='recorded',description='Повторный бросок урона этой чертой пока не автоматизирован.'),
 'skilled':choice('Умелец',status='implemented',description='Три дополнительных владения навыками из выбранного списка.'),
 'magic_initiate_cleric':choice('Посвящённый в магию: жрец',status='recorded',description='Два заговора и одно заклинание первого круга; выбор проверяется. Применение магии пока не автоматизировано.'),
 'magic_initiate_wizard':choice('Посвящённый в магию: волшебник',status='recorded',description='Два заговора и одно заклинание первого круга; выбор проверяется. Применение магии пока не автоматизировано.'),
}

# Curated legal spell choices rather than unconstrained strings or a claim of full SRD coverage.
SPELLS={
 'light':('Свет',0,['wizard','cleric']), 'mage_hand':('Волшебная рука',0,['wizard']),
 'ray_of_frost':('Луч холода',0,['wizard']), 'fire_bolt':('Огненный снаряд',0,['wizard']),
 'prestidigitation':('Фокусы',0,['wizard']), 'mending':('Починка',0,['wizard','cleric']),
 'guidance':('Наставление',0,['cleric']), 'sacred_flame':('Священное пламя',0,['cleric']),
 'thaumaturgy':('Чудотворство',0,['cleric']), 'resistance':('Сопротивление',0,['cleric']),
 'bless':('Благословение',1,['cleric']), 'cure_wounds':('Лечение ран',1,['cleric']),
 'healing_word':('Лечащее слово',1,['cleric']), 'command':('Приказ',1,['cleric']),
 'sanctuary':('Убежище',1,['cleric']), 'shield_of_faith':('Щит веры',1,['cleric']),
 'inflict_wounds':('Нанесение ран',1,['cleric']),
 'detect_magic':('Обнаружение магии',1,['wizard','cleric']), 'protection_evil_good':('Защита от зла и добра',1,['wizard','cleric']),
 'shield':('Щит',1,['wizard']), 'magic_missile':('Волшебная стрела',1,['wizard']),
 'mage_armor':('Доспехи мага',1,['wizard']), 'feather_fall':('Падение пёрышком',1,['wizard']),
 'burning_hands':('Огненные ладони',1,['wizard']), 'alarm':('Сигнал тревоги',1,['wizard']),
}

SPECIES_2014={
 'human':choice('Человек',speed=30,sizes=['Medium'],bonuses={a:1 for a in ABILITIES},bonus_choices=0,skill_count=0,languages=['common'],language_count=1,features=[],hp_bonus=0,page=5),
 'half_elf':choice('Полуэльф',speed=30,sizes=['Medium'],bonuses={'charisma':2},bonus_choices=2,skill_count=2,languages=['common','elvish'],language_count=1,features=['darkvision','fey_ancestry'],hp_bonus=0,page=6),
 'hill_dwarf':choice('Холмовой дварф',speed=25,sizes=['Medium'],bonuses={'constitution':2,'wisdom':1},bonus_choices=0,skill_count=0,languages=['common','dwarvish'],language_count=0,features=['darkvision','dwarven_resilience','stonecunning','dwarven_toughness'],hp_bonus=1,page=4),
}
SPECIES_2024={
 'human':choice('Человек',speed=30,sizes=['Small','Medium'],bonuses={},bonus_choices=0,skill_count=1,languages=['common'],language_count=2,features=['human_resourceful'],hp_bonus=0,page=86,origin_feat=True),
 'dwarf':choice('Дварф',speed=30,sizes=['Medium'],bonuses={},bonus_choices=0,skill_count=0,languages=['common'],language_count=2,features=['darkvision','dwarven_resilience','stonecunning','dwarven_toughness'],hp_bonus=1,page=84),
}
BACKGROUNDS_2014={
 'acolyte':choice('Послушник',skills=['insight','religion'],abilities=[],tools=[],languages=2,features=['shelter_faithful'],page=61),
}
BACKGROUNDS_2024={
 'acolyte':choice('Послушник',skills=['insight','religion'],abilities=['intelligence','wisdom','charisma'],tools=['calligrapher'],languages=0,features=['magic_initiate_cleric'],page=83),
 'criminal':choice('Преступник',skills=['sleight_of_hand','stealth'],abilities=['dexterity','constitution','intelligence'],tools=['thieves'],languages=0,features=['alert'],page=83),
 'sage':choice('Мудрец',skills=['arcana','history'],abilities=['constitution','intelligence','wisdom'],tools=['calligrapher'],languages=0,features=['magic_initiate_wizard'],page=83),
 'soldier':choice('Солдат',skills=['athletics','intimidation'],abilities=['strength','dexterity','constitution'],tools=['gaming'],languages=0,features=['savage_attacker'],page=83),
}
TOOLS={'thieves':'Воровские инструменты','calligrapher':'Принадлежности каллиграфа','gaming_dice':'Игральные кости','gaming_cards':'Игральные карты','smith':'Инструменты кузнеца','mason':'Инструменты каменщика','brewer':'Принадлежности пивовара'}
FIGHTING_STYLES={'defense':'Защита','archery':'Стрельба','great_weapon':'Бой большим оружием','two_weapon':'Бой двумя оружиями'}
SUBCLASSES={'champion':'Чемпион','thief':'Вор','evocation':'Школа воплощения','life':'Домен жизни'}

def catalog(ruleset):
    if ruleset not in SOURCES:raise ValueError('Конструктор поддерживает только явно выбранные D&D 2014 и D&D 2024')
    classes=copy.deepcopy(BASE_CLASSES)
    if ruleset=='dnd2024':
        for row in classes.values():row['subclass_level']=3;row['page']=row['page2024']
        classes['fighter']['skills'].append('persuasion');classes['fighter']['features'].append('weapon_mastery')
        classes['rogue']['skills'].remove('performance');classes['rogue']['specific_weapons']=['scimitar','shortsword','rapier','whip','hand_crossbow'];classes['rogue']['features'].append('weapon_mastery')
        classes['wizard']['weapons']=['simple'];classes['wizard']['specific_weapons']=[];classes['wizard']['skills'].append('nature');classes['wizard']['features'].append('ritual_adept')
        classes['cleric']['features']=['spellcasting','divine_order']
    return dict(version=1,ruleset=ruleset,source=SOURCES[ruleset],levels=[1],classes=classes,
        species=copy.deepcopy(SPECIES_2014 if ruleset=='dnd2014' else SPECIES_2024),
        backgrounds=copy.deepcopy(BACKGROUNDS_2014 if ruleset=='dnd2014' else BACKGROUNDS_2024),
        abilities=ABILITY_NAMES,standard=STANDARD,point_cost=POINT_COST,point_budget=27,
        skills={k:dict(name=SKILL_NAMES[k],ability=v) for k,v in SKILLS.items()},languages=LANGUAGES,
        standard_languages=STANDARD_2024 if ruleset=='dnd2024' else ['common','dwarvish','elvish','giant','gnomish','goblin','halfling','orc'],tools=TOOLS,
        features=FEATURES,spells={k:dict(name=v[0],level=v[1],classes=v[2],status='recorded') for k,v in SPELLS.items()},
        fighting_styles=FIGHTING_STYLES,subclasses=SUBCLASSES,
        coverage='Проверенный каталог первого уровня: четыре класса. Часть видов и вариантов SRD пока не включена. Эффекты способностей отмечены отдельно; Level Up и полное применение магии не реализованы.')
