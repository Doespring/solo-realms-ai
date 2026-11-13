"""Explicit coverage of trusted engine handlers, separated from PDF candidates.

Implemented means only the listed narrow scope. A whole rules category is never
declared complete from one supported operation. No PDF/AI data is dispatched.
"""
from .rulesets import SOURCES

ROWS=(
 ('checks.ability','checks','implemented','RulesetEngine.check','Проверки характеристик, навыки, владение, преимущество/помеха','Групповые и состязательные процедуры требуют отдельной регистрации'),
 ('combat.weapon_attack','combat','implemented','CombatEngine.attack','Зарегистрированные атаки оружием, КБ, попадание, урон','Только зарегистрированные участники, оружие и профиль боя'),
 ('combat.turn','combat','partial','CombatEngine','Очередь, перемещение, реакции и зарегистрированные боевые действия','Полный набор действий, особые атаки монстров и все исключения не покрыты'),
 ('magic.casting','magic','unsupported',None,'Нет универсального исполнителя заклинаний','Каталог/запись заклинания не исполняет его эффект'),
 ('features.execution','features','partial','character_creation.evaluate','Валидируемые особенности и отдельные зарегистрированные эффекты','Не все классовые, расовые особенности и черты исполняются'),
 ('conditions.execution','conditions','partial','CombatEngine','Поддержанный набор состояний в зарегистрированном бою','Не все взаимодействия состояний и специальные исключения покрыты'),
 ('rest.recovery','rest','partial','RulesetEngine.rest','Короткий/длительный отдых, кости хитов и восстановление ресурсов по редакции','Прерывания, опасная среда и все исключения требуют отдельных процедур'),
 ('advancement.level_up','advancement','partial','nat20_levelup.level_up','Подтверждённое повышение уровня пересчитывает Nat20 по опыту кампании или назначенному рубежу','Мультикласс и повышение вне журнала кампании не покрыты'),
 ('character.level_one','advancement','partial','character_creation.evaluate','Проверяемые варианты создания героя первого уровня','Не весь материал PHB и не полный диапазон уровней'),
)

# A row that exists in one edition only says so here. Advancement is SRD 5.2,
# so the 2014 registry keeps reporting it as unsupported rather than claiming a
# handler that would refuse the edition.
EDITIONS={'advancement.level_up':('dnd2024',)}

def coverage(edition=None):
    if edition is not None and edition not in ('dnd2014','dnd2024'):raise ValueError('Неизвестная редакция')
    editions=[edition] if edition else ['dnd2014','dnd2024']
    return [dict(id=id,edition=e,category=category,
        status=(status if e in EDITIONS.get(id,('dnd2014','dnd2024')) else 'unsupported'),
        handler=(handler if e in EDITIONS.get(id,('dnd2014','dnd2024')) else None),
        scope=scope,limitations=limitations,source=SOURCES[e[-4:]],
        source_kind='existing_srd_engine_subset',source_pages=[],
        supplied_book_crosscheck='pending',pdf_import_can_activate=False)
        for e in editions for id,category,status,handler,scope,limitations in ROWS]

def status(operation,edition):
    row=next((r for r in coverage(edition) if r['id']==operation),None)
    return row['status'] if row else 'unsupported'
