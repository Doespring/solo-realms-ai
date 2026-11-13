"""r176: дорога, укрытия, поиск и груз считаются по числам книги.

Глава 1 книги («Исследование») держит таблицу темпа, эффекты темпа, условия
укрытия, поиск в объявленной области, грузоподъёмность и перечень угроз.
До r176 эти записи корпуса стояли как `blocked`: числа выписаны, обработчика
нет. Здесь обработчик хоста: он читает числа из `facts` самой записи корпуса,
считает расстояние/груз/условия и **всегда** бросает кости движком
(`RulesetEngine`) — второго калькулятора не появляется.

Три источника чисел, и каждый назван в `SOURCES`:

* `rules_data/v2/chapters/phb-01/03-exploration.rules.json` — таблица темпа,
  эффекты темпа, удвоение верхом, транспорт, условия Засады и Поиска,
  заслонённая область, примеры экипировки и угрозы;
* `Руководство Мастера 2024 v1.1`, с. 34 — форсированный марш (книга игрока
  этого правила не содержит: в её разделе «Путешествие» только темп,
  транспорт и походный строй). Страница проверяется тестом, когда книга есть
  на диске;
* `SRD_CC_v5.2.1.pdf` (CC-BY-4.0, с. 178/183/187) — таблица грузоподъёмности
  по размеру, Сл 15 скрытности у Засады, характеристика Поиска.

Чат получает это через `host_intent`: он возвращает пару (сводка, переход),
переход вызывается `Store.record_scenario` — тем же журналом, что и остальные
проверенные ходы. Отказ (нет укрытия, область не объявлена, перегруз) — это
`ValueError` с причиной, а не молчаливый ноль.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAPTER = ROOT / 'rules_data' / 'v2' / 'chapters' / 'phb-01' / '03-exploration.rules.json'

#: Числа вне главы 1 — с точной страницей и цитатой. Тест
#: `test_source_pages_carry_these_numbers` вытаскивает страницы из PDF и
#: сверяет их; нет книги на диске — честный skip с причиной.
SOURCES = {
    'forced_march': dict(
        document='dmg_2024_ru_v1_1', pdf_page=34,
        quote='В конце каждого дополнительного часа путешествия после 8 часов каждый персонаж '
              'должен преуспеть в спасброске Телосложения или получить 1 степень истощения. '
              'Сложность спасброска равна 10 + 1 за каждый час сверх 8 часов.',
        base_hours=8, dc_base=10, dc_per_hour=1, exhaustion_per_failure=1),
    'carrying': dict(
        document='srd_5.2.1', pdf_page=178,
        quote='Carry / Drag/Lift/Push: Tiny Str. x 7.5 / 15; Small-Medium x 15 / 30; '
              'Large x 30 / 60; Huge x 60 / 120; Gargantuan x 120 / 240 lb.',
        carry={'Tiny': 7.5, 'Small': 15, 'Medium': 15, 'Large': 30, 'Huge': 60, 'Gargantuan': 120},
        drag_multiplier=2, overload_speed=5),
    'hiding': dict(document='srd_5.2.1', pdf_page=183,
                   quote='you must succeed on a DC 15 Dexterity (Stealth) check while you are '
                         'Heavily Obscured or behind Three-Quarters Cover or Total Cover.',
                   dc=15),
    'search': dict(document='srd_5.2.1', pdf_page=187,
                   quote='Search: Perception — Concealed creature or object.'),
}

#: Покрытия, которых достаточно для Засады (книга: 3/4, полное или сильное
#: заслонение). «Половина» не годится — и это проверяется отказом.
HIDE_COVERS = ('three_quarters', 'total', 'heavy_obscurement')

#: Слова темпа в реплике игрока. Форсированный марш — не темп таблицы, а
#: решение идти дольше дня; он считается отдельно.
PACE_WORDS = (
    ('fast', r'быстр\w*'),
    ('normal', r'(?:нормальн|обычн|средн)\w*'),
    ('slow', r'медленн\w*'),
)
FORCED = r'форсированн\w*'

_facts_cache = {}


def _chapter():
    if not _facts_cache:
        raw = CHAPTER.read_text(encoding='utf-8')
        _facts_cache['rules'] = {row['id'].rsplit('.', 1)[-1]: row
                                 for row in json.loads(raw)['rules']}
    return _facts_cache['rules']


def facts(name):
    """`facts` записи корпуса — единственный источник чисел главы."""
    row = _chapter().get(name)
    if row is None:
        raise KeyError('В корпусе нет правила исследования: ' + name)
    return row['facts']


def norm(text):
    return ' '.join(str(text or '').lower().replace('ё', 'е').split())


# ---------------------------------------------------------------- темп пути

def pace_table():
    """Таблица «Темп перемещения» столбцами, а не тремя несогласованными списками."""
    data = facts('travel_pace')
    return {pace: dict(feet_per_minute=feet, miles_per_hour=hour, miles_per_day=day)
            for pace, feet, hour, day in zip(data['paces'], data['feet_per_minute'],
                                             data['miles_per_hour'], data['miles_per_day'])}


def pace_effects(pace):
    """Игровые эффекты темпа: навык, характеристика и режим броска."""
    if pace not in pace_table():
        raise ValueError('Неизвестный темп путешествия: ' + str(pace))
    data = facts('travel_pace')
    abilities = {'perception': data['perception_survival_ability'],
                 'survival': data['perception_survival_ability'],
                 'stealth': data['stealth_ability']}
    table = {'fast': (data['fast_disadvantage_skills'], 'disadvantage'),
             'normal': (data['normal_disadvantage_skills'], 'disadvantage'),
             'slow': (data['slow_advantage_skills'], 'advantage')}
    skills, mode = table[pace]
    return [dict(skill=skill, ability=abilities[skill], mode=mode) for skill in skills]


def leg(pace, hours, *, mounted=False):
    """Расстояние за `hours` часов выбранным темпом.

    Столбец «День» — отдельная величина книги, а не произведение часов на
    скорость: медленный темп даёт 18 миль в день, хотя 2 мили × 8 часов = 16,
    а быстрый — 30 миль, хотя 4 × 8 = 32. Поэтому целый день берётся из столбца
    «День», часть дня — из столбца «Час», а часы сверх дня (форсированный марш)
    добавляются часовыми милями. Пересчитывать столбец дня из часов значит
    выдумывать числа, чего запись корпуса прямо запрещает.
    """
    if hours < 0:
        raise ValueError('Часы пути не могут быть отрицательными')
    row = pace_table()[pace]
    day_hours = SOURCES['forced_march']['base_hours']
    if hours < day_hours:
        miles = row['miles_per_hour'] * hours
    elif hours == day_hours:
        miles = row['miles_per_day']
    else:
        miles = row['miles_per_day'] + row['miles_per_hour'] * (hours - day_hours)
    if mounted:
        # Единственное место, где ступень удвоения ещё уместна: расстояние одного
        # отрезка верхом. Верхом за день считается в `mounted_leg`.
        miles = round(miles * facts('mounted_travel')['distance_multiplier'], 2)
    return dict(pace=pace, hours=hours, miles=miles, mounted=bool(mounted),
                feet_per_minute=row['feet_per_minute'], miles_per_hour=row['miles_per_hour'],
                miles_per_day=row['miles_per_day'])


def mounted_leg(pace, hours, *, used_hours=0):
    """Удвоение только на один час; дальше скакуну нужен отдых.

    Книга: «верхрем на скакунах группа может переместиться на вдвое большее
    расстояние за 1 час, после чего животным потребуется Короткий или Долгий
    отдых». Отдых возвращает право на час (счётчик обнуляется хостом).
    """
    rule = facts('mounted_travel')
    if hours > rule['duration_hours'] and used_hours >= rule['duration_hours']:
        raise ValueError('Скакун уже прошёл час удвоенным темпом: нужен Короткий или Долгий '
                         'отдых, иначе удвоения нет')
    if hours <= 0:
        raise ValueError('Часы пути должны быть больше нуля')
    doubled = min(hours, rule['duration_hours'] - used_hours)
    # Удвоение — это один лишний час пути, а не другой столбец таблицы: пеший
    # день остаётся днём (30/24/18 миль), а верхом к нему добавляется час
    # (4/3/2 мили). Иначе 8 часов верхом давали бы 36 миль вместо 34.
    miles = leg(pace, hours)['miles'] + (pace_table()[pace]['miles_per_hour'] if doubled else 0)
    return dict(pace=pace, hours=hours, miles=round(miles, 2),
                double_hours=doubled, mount_hours_after=used_hours + doubled,
                refresh=rule['refresh'], mounted=True,
                miles_per_hour=pace_table()[pace]['miles_per_hour'])


def rest_mount(state):
    """Короткий или Долгий отдых возвращает право на удвоенный час."""
    travel = state.setdefault('travel', {})
    travel['mount_hours'] = 0
    return travel


# ------------------------------------------------------- форсированный марш

def forced_march_slots(hours):
    """Слоты спасбросков Телосложения за часы сверх дня."""
    rule = SOURCES['forced_march']
    extra = int(hours) - rule['base_hours']
    return [dict(hour=rule['base_hours'] + index + 1,
                 dc=rule['dc_base'] + rule['dc_per_hour'] * (index + 1))
            for index in range(max(0, extra))]


def forced_march(hero, hours, rng=None, state=None):
    """Спасбросок за каждый час сверх 8; провал — степень истощения.

    Кости бросает движок: хост только называет Сл и применяет последствие.
    Смертельное истощение (6) не применяется молча — это отдельное решение.
    """
    from .rulesets import RulesetEngine, active
    engine = RulesetEngine(active(state or {}))
    slots, level = [], hero.exhaustion
    for slot in forced_march_slots(hours):
        result = engine.saving_throw(hero, 'constitution', slot['dc'], rng=rng)
        failed = not result['success']
        if failed:
            level = min(6, level + SOURCES['forced_march']['exhaustion_per_failure'])
        slots.append({**slot, 'save': result, 'failed': failed, 'exhaustion_after': level})
    if level >= 6:
        raise ValueError('Форсированный марш доводит истощение до 6 (механическая смерть): '
                         'это отдельное решение о последствиях, а не автоматический переход')
    hero.exhaustion = level
    return dict(hours=hours, extra_hours=max(0, hours - SOURCES['forced_march']['base_hours']),
                exhaustion=level, slots=slots)


# ------------------------------------------------------------ укрытия, скрытность

def hide_conditions(*, cover='none', out_of_sight=True, pace=None, heavy_obscurement=False):
    """Можно ли прятаться: условия Засады из книги, с причиной отказа."""
    cover = 'heavy_obscurement' if heavy_obscurement else cover
    if cover not in ('none', 'named', 'half', 'three_quarters', 'total', 'heavy_obscurement'):
        raise ValueError('Неизвестное укрытие: ' + str(cover))
    if cover not in HIDE_COVERS:
        if cover == 'none':
            reason = ('укрытие не объявлено: спрятаться можно только за укрытием 3/4, полным '
                      'укрытием или в сильно заслонённой области')
        elif cover == 'named':
            reason = ('названное место укрытием не считается: нужен предмет или область, за '
                      'которой можно укрыться (3/4, полное укрытие или сильное заслонение)')
        else:
            reason = ('половины укрытия для Засады недостаточно: нужно 3/4, полное укрытие '
                      'или сильное заслонение')
        return dict(allowed=False, reason=reason, cover=cover)
    if not out_of_sight:
        return dict(allowed=False, cover=cover,
                    reason='героя видно: Засада требует быть вне линии обзора врагов')
    dc = SOURCES['hiding']['dc']
    effects = pace_effects(pace) if pace else []
    mode = next((item['mode'] for item in effects if item['skill'] == 'stealth'), None)
    return dict(allowed=True, cover=cover, dc=dc, skill='stealth',
                ability=facts('travel_pace')['stealth_ability'],
                disadvantage=mode == 'disadvantage', advantage=mode == 'advantage',
                ends_on=['шум громче шёпота', 'враг нашёл героя', 'бросок атаки',
                         'заклинание со словесным компонентом'])


def hide_roll(hero, *, cover='none', out_of_sight=True, pace=None, heavy_obscurement=False,
              rng=None, state=None):
    """Засада: условия, затем Сл из книги; кость — движком."""
    from .rulesets import RulesetEngine, active
    conditions = hide_conditions(cover=cover, out_of_sight=out_of_sight, pace=pace,
                                 heavy_obscurement=heavy_obscurement)
    if not conditions['allowed']:
        raise ValueError(conditions['reason'])
    check = RulesetEngine(active(state or {})).skill(
        hero, 'stealth', conditions['dc'], rng=rng,
        disadvantage=conditions['disadvantage'], advantage=conditions['advantage'])
    return dict(conditions=conditions, check=check, hidden=check['success'],
                hiding_dc=check['total'] if check['success'] else None)


def obscurement(level):
    """Заслонённая область: что она значит механически."""
    if level == 'heavy':
        data = facts('heavy_obscurement')
        return dict(level='heavy', vision=data['vision'],
                    see_into=data['condition_when_seeing_into'],
                    hide_allowed=True, examples=data['examples'],
                    note='Сильно заслонённая область: видеть внутрь нельзя, а прятаться в ней можно')
    if level == 'light':
        return dict(level='light', vision='lightly_obscured', see_into='disadvantage_perception',
                    hide_allowed=False,
                    note='Слабо заслонённая область: помеха на Восприятие, но для Засады мало')
    raise ValueError('Неизвестная заслонённость: ' + str(level))


# ------------------------------------------------------------------- поиск

ENDINGS = ('ами', 'ями', 'ах', 'ях', 'ов', 'ев', 'ой', 'ей', 'ом', 'ем',
           'а', 'я', 'у', 'ю', 'е', 'ы', 'и')


def _stem(text):
    """Грубый стем последнего слова: «сундуке» и «сундук» — одно место."""
    words = [word for word in norm(text).split()
             if word not in ('в', 'во', 'на', 'за', 'под', 'у', 'внутри', 'возле', 'около')]
    if not words:
        return ''
    word = words[-1]
    for ending in ENDINGS:
        if len(word) - len(ending) >= 4 and word.endswith(ending):
            return word[:-len(ending)]
    return word


def search_area(declared, hidden=None):
    """Поиск идёт только в объявленной области; вне её проверка не поможет."""
    declared = norm(declared)
    if not declared or declared in ('везде', 'всюду', 'вокруг', 'тут', 'здесь', 'рядом'):
        return dict(allowed=False,
                    reason='область поиска не объявлена: назовите, где именно искать '
                           '(в сундуке, за камином, под кроватью)')
    if hidden is not None and _stem(hidden) != _stem(declared):
        return dict(allowed=False, declared=declared, hidden=norm(hidden),
                    reason='объект вне объявленной области: проверка Восприятия не обнаружит '
                           'его независимо от результата')
    return dict(allowed=True, declared=declared, ability=facts('hidden_objects')['ability'],
                skill=facts('hidden_objects')['skill'])


def search(hero, declared, hidden=None, *, dc=None, pace=None, rng=None, state=None):
    """Поиск скрытого объекта: область, затем проверка Мудрости (Восприятие)."""
    from .rulesets import RulesetEngine, active
    area = search_area(declared, hidden)
    if not area['allowed']:
        raise ValueError(area['reason'])
    effects = pace_effects(pace) if pace else []
    mode = next((item['mode'] for item in effects if item['skill'] == area['skill']), None)
    engine = RulesetEngine(active(state or {}))
    check = engine.skill(hero, area['skill'], dc, rng=rng,
                         disadvantage=mode == 'disadvantage', advantage=mode == 'advantage') \
        if dc is not None else engine.d20(hero, area['ability'], kind='check',
                                          proficient=area['skill'] in hero.skill_proficiencies,
                                          rng=rng, disadvantage=mode == 'disadvantage',
                                          advantage=mode == 'advantage')
    success = check.get('success')
    return dict(area=area, check=check, success=success,
                finds=facts('hidden_objects')['success'] if success else None)


# ---------------------------------------------------------------- груз

def carrying(hero):
    """Грузоподъёмность: размер × Сила (SRD 5.2.1, с. 178)."""
    size = hero.size
    multiplier = SOURCES['carrying']['carry'].get(size)
    if multiplier is None:
        raise ValueError('Неизвестный размер существа: ' + str(size))
    strength = hero.abilities['strength']
    carry = multiplier * strength
    return dict(size=size, strength=strength, multiplier=multiplier, carry_lb=carry,
                drag_lb=carry * SOURCES['carrying']['drag_multiplier'],
                overload_speed=SOURCES['carrying']['overload_speed'])


def load(hero, weight_lb, *, mode='carry'):
    """Можно ли нести/тащить вес; перегруз — отказ с причиной, не тишина."""
    if weight_lb < 0:
        raise ValueError('Вес не может быть отрицательным')
    limits = carrying(hero)
    ceiling = limits['drag_lb'] if mode in ('drag', 'lift', 'push') else limits['carry_lb']
    if weight_lb > ceiling:
        raise ValueError('Перегруз: %s фунтов больше предела %s (размер %s, Сила %s, '
                         'грузоподъёмность %s) — нужно оставить часть груза или взять транспорт'
                         % (weight_lb, ceiling, hero.size, limits['strength'], limits['carry_lb']))
    crowded = mode in ('drag', 'lift', 'push') and weight_lb > limits['carry_lb']
    return dict(**limits, mode=mode, weight_lb=weight_lb, allowed=True,
                speed_ft=limits['overload_speed'] if crowded else None,
                note='Скорость не выше 5 футов, пока тащите больше грузоподъёмности'
                if crowded else 'Вес в пределах грузоподъёмности')


# ------------------------------------------------------- верхом и транспорт

def vehicle(kind, *, hours=8):
    """Темп транспорта: наземный — обычный, водный — скорость судна."""
    data = facts('vehicles')
    kind = norm(kind)
    if kind in ('water', 'boat', 'ship', 'vessel', 'вода', 'лодка', 'корабль', 'судно'):
        return dict(kind='water', pace_choice=False, hours=min(hours, data['max_possible_hours_per_day']),
                    max_hours=data['max_possible_hours_per_day'],
                    depends_on=data['water_hours_depend_on'],
                    note='На судне темп не выбирают: скорость задаёт судно, часы — тип судна и '
                         'численность экипажа (глава 6)')
    if kind in ('land', 'wagon', 'carriage', 'повозка', 'фургон', 'карета', 'телега'):
        pace = data['land_vehicle_pace']
        day_hours = min(hours, SOURCES['forced_march']['base_hours'])
        row = leg(pace, day_hours)
        return dict(kind='land', pace=pace, pace_choice=True, hours=day_hours,
                    miles=row['miles'], miles_per_hour=row['miles_per_hour'],
                    miles_per_day=row['miles_per_day'],
                    note='Наземный транспорт едет обычным темпом; часы сверх дня считаются '
                         'форсированным маршем, а не молчаливым потолком')
    raise ValueError('Неизвестный транспорт: ' + str(kind))


# --------------------------------------------- разрушение, чувства, угрозы

def destroy(thing):
    """Хрупкий немагический объект ломается действием автоматически."""
    data = facts('destroy_fragile_object')
    name = norm(thing)
    if not name:
        raise ValueError('Не назван объект: что именно герой ломает?')
    if re.search(r'магическ|зачарован|волшебн', name):
        raise ValueError('Магический объект не ломается автоматически: правило главы 1 '
                         'говорит только о немагическом')
    if re.search(r'камен|железн|металл|стальн|гранитн|прочн|дубов', name):
        raise ValueError('Прочный объект требует правил урона по объектам из глоссария; '
                         'автоматическое разрушение — только для хрупкого')
    if not re.search(r'стекл|бумаг|хрупк|глинян|фарфор|ампул|свеч|пузыр|склянк|тарелк|ваз|сосуд'
                     r'|трубк|лист', name):
        raise ValueError('Прочность не подтверждена: автоматически ломается только хрупкий '
                         'предмет (стекло, бумага, глиняная посуда); для прочного — правила '
                         'урона по объектам')
    return dict(object=name, automatic=True, action_cost=data['action_cost'],
                conditions=data['automatic_if'],
                note='Действие: объект уничтожен; прочные объекты — отдельные правила урона')


def special_senses():
    """Глава даёт только имена чувств; диапазоны живут в бестиарии."""
    data = facts('special_senses')
    return dict(senses=data['senses'], definitions_in=data['definitions_in'],
                ranges_known=False,
                note='Диапазоны и полные эффекты особых чувств глава не задаёт: они в блоке '
                     'существа, и хост их не выдумывает')


def hazards():
    """Перечень угроз главы 1 без чисел: числа — в глоссарии."""
    data = facts('hazards')
    return dict(hazards=data['hazards'], definitions_in=data['definitions_in'],
                numbers_known=False,
                note='Глава перечисляет угрозы и отправляет за числами в глоссарий; '
                     'урон и Сл хост не подставляет')


def exploration_equipment():
    """Экипировка исследования: применимость решает Мастер, механику — глава 6."""
    data = facts('exploration_equipment')
    return dict(examples=data['examples'], rules_chapter=data['equipment_rules_chapter'],
                adjudication_required=True,
                note='Примеры экипировки не являются определениями предметов: '
                     'применимость подтверждает Мастер, механику задаёт глава 6')


# ---------------------------------------------------------------- разбор реплики

# ---------------------------------------------------------------- разбор реплики

#: Слова, которые действительно называют укрытие. «В комнате» укрытием не
#: считается: качество укрытия решает Мастер, а хост только проверяет, что
#: игрок его объявил, и называет своё прочтение.
COVER_WORDS = (r'бочк|ящик|угол\b|куст|дерев|стен|скал|занавес|тень|тени|укрыт|повозк|стол\b'
               r'|камин|колонн|балкон|валун|сундук|шкаф|ниш|расщелин|канав|окоп')
OBSCURE_WORDS = r'туман|темнот|листв|дым|сумрак|мгла'

#: Темп — только там, где о темпе и говорят. «Дорогая вещь выглядит обычной»
#: не должна становиться объявлением темпа.
PACE_GATE = r'темп|марш|скорост'
MOVEMENT_WORDS = r'идем|идти|пойдем|двигаемся|в путь'


def parse_pace(text):
    """Темп из реплики: темп, часы (или None) и форсированный марш."""
    said = norm(text)
    adverbs = re.search(r'быстро|медленно', said) and re.search(MOVEMENT_WORDS, said)
    if not (re.search(PACE_GATE, said) or adverbs):
        return None
    forced = bool(re.search(FORCED, said))
    pace = None
    for name, pattern in PACE_WORDS:
        if re.search(pattern, said):
            pace = name
            break
    if pace is None and not forced:
        return None
    hours = None
    match = re.search(r'(\d{1,2})\s*час', said)
    if match:
        hours = int(match.group(1))
    elif re.search(r'весь день|за день|до вечера|до темноты|полный день', said):
        hours = SOURCES['forced_march']['base_hours']
    return dict(pace=pace or 'normal', hours=hours, forced=forced)


def declared_area(text):
    """Что именно игрок объявил областью поиска — после глагола и предлога."""
    said = norm(text)
    said = re.sub(r'^(?:я\s+)?(?:ищу|обыскиваю|осматриваю|проверяю|обшариваю)\s+', '', said)
    said = re.sub(r'^(?:ловушку|ловушки|тайник|тайники|потайную дверь|потайной двери'
                  r'|скрытую дверь|скрытый ход|скрытые детали)\s*', '', said)
    said = re.sub(r'^(?:в|во|на|за|под|у|внутри|возле|около)\s+', '', said)
    return re.sub(r'[.!?]+$', '', said).strip()


EXAMINE_OBJECT = re.compile(r'^(?:я\s+)?(?:осматриваю|изучаю|рассматриваю)\s+(.+?)[.!?]*$')
HIDDEN_THINGS = re.compile(r'ловушк|тайник|потайную|скрыт|секретн|спрятанн|западн')


def declared_examine(state, said):
    """Имя зарегистрированного объекта в «осматриваю <объект>», иначе None.

    Осмотр предмета ведёт `interactions` (описание объекта). Хостовый поиск
    ловит только скрытое, поэтому «осматриваю дверь» не должен превращаться
    в бросок Восприятия: иначе игрок не видит описание, а событие приходит
    без `action`. Явно названное скрытое («осматриваю дверь в поисках тайника»)
    остаётся поиском.
    """
    if HIDDEN_THINGS.search(said):
        return None
    match = EXAMINE_OBJECT.match(said.strip('.!? ').strip())
    if not match:
        return None
    from .interactions import locate
    try:
        _, found = locate(state, match.group(1).strip('.! '))
    except (KeyError, StopIteration, ValueError):
        return None
    return found[0].name if found else None


def host_intent(state, text):
    """Реплика о дороге/укрытии/поиске/грузе -> (сводка, переход) или None.

    Переход — доверенный код: он меняет состояние и возвращает структурные
    результаты того же журнала, что и остальные проверенные ходы. Кости
    бросает движок внутри перехода, поэтому повтор запроса с тем же
    `request_id` отдаёт событие из журнала, а не новый бросок.
    """
    said = norm(text)
    if not said:
        return None
    # Темп, укрытие, поиск и груз — правила вне сражения: в бою ходом владеет
    # движок, и хост не подменяет его своим переходом.
    if state.get('combat', {}).get('active'):
        return None

    pace = parse_pace(said)
    if pace:
        return ('Темп путешествия', _pace_transition(pace))

    if re.search(r'прячусь|скрываюсь|затаиваюсь|спрятаться|спрятался', said):
        if re.search(OBSCURE_WORDS, said):
            cover = 'heavy_obscurement'
        elif (re.search(r'(?<![\w-])(?:за|под|в)\s+\w+', said) and re.search(COVER_WORDS, said)
              and not re.search(r'на открытом|посреди|без укрыт|на виду', said)):
            cover = 'three_quarters'
        elif re.search(r'(?<![\w-])(?:за|под|в)\s+\w+', said):
            cover = 'named'      # место названо, но укрытием не является
        else:
            cover = 'none'
        return ('Засада', _hide_transition(cover,
                                          out_of_sight=not re.search(r'на виду|под прицелом', said)))

    if re.search(r'(?:смотрю|вглядываюсь|всматриваюсь|вхожу|иду|шагаю)\s+(?:в|во|сквозь)\s+'
                 r'(?:туман|темнот|листв|дым|сумрак|мглу)', said):
        return ('Заслонённая область', _obscurement_transition())

    # Поиск скрытого, а не любое «ищу»: реплику «ищу травы» ведёт прежний
    # обработчик, иначе механический отказ съедал бы обычное действие.
    if (re.search(r'обыскиваю|осматриваю|ищу\s+(?:ловушк|тайник|потайную|скрыт|секретн|спрятанн)'
                 r'|ищу\s+(?:в|во|на|за|под|у|внутри|возле)\s+\w+|потайную|ловушк|тайник', said)
            and not declared_examine(state, said)):
        return ('Поиск', _search_transition(said))

    weight = re.search(r'(\d{1,4})\s*(?:фунт|фнт|lb)', said)
    if weight and re.search(r'беру|поднимаю|несу|тащу|волоку|перетаскиваю', said):
        return ('Груз', _load_transition(int(weight.group(1)),
                                         'drag' if re.search(r'тащу|волоку|перетаскиваю', said)
                                         else 'carry'))

    if re.search(r'верхом|скачу|скачем|на коне\b|сажусь на коня', said):
        hours = int((re.search(r'(\d{1,2})\s*час', said) or [None, 1])[1])
        return ('Верхом', _mounted_transition(hours))

    if re.search(r'плыв[уе]|плывем|отплыва|на лодке|в лодке|на корабле|на судне|на повозке'
                 r'|в фургоне|в карете|на телеге', said):
        kind = 'water' if re.search(r'плыв|лодк|корабл|судн', said) else 'land'
        hours = int((re.search(r'(\d{1,2})\s*час', said) or [None, 8])[1])
        return ('Транспорт', _vehicle_transition(kind, hours))

    if re.search(r'\bломаю|\bразрушаю|\bуничтожаю|\bрву\b', said):
        return ('Разрушение объекта', _destroy_transition(said))
    if re.search(r'особ(?:ое|ые) чувств|слепое зрение|темное зрение|истинное зрение', said):
        return ('Особые чувства', _adjudication_transition('special_senses', special_senses))
    if re.search(r'\bугроз|обезвоживан|удушь|недоедан', said):
        return ('Угрозы', _adjudication_transition('hazards', hazards))
    return None


def _pace_transition(parsed):
    def transition(state):
        from .contracts import Hero
        hours, forced = parsed['hours'], parsed['forced']
        if forced and hours is None:
            raise ValueError('Форсированный марш требует числа часов: скажите, сколько часов идти')
        if forced and hours <= SOURCES['forced_march']['base_hours']:
            raise ValueError('Форсированный марш начинается после %s часов пути: часов должно быть '
                             'больше' % SOURCES['forced_march']['base_hours'])
        hero = Hero.model_validate(state['hero'])
        travel = state.setdefault('travel', {})
        travel.update(pace=parsed['pace'], forced=bool(forced))
        row = pace_table()[parsed['pace']]
        effects = pace_effects(parsed['pace'])
        result = dict(type='scripted', kind='travel_pace', pace=parsed['pace'], hours=hours,
                      feet_per_minute=row['feet_per_minute'], miles_per_hour=row['miles_per_hour'],
                      miles_per_day=row['miles_per_day'], effects=effects)
        if hours is None:
            # Темп без срока: игрок объявил, как идти, но не сказал, сколько —
            # хост не выдумывает полный день пути, а записывает только темп.
            travel.pop('hours', None)
            travel.pop('miles', None)
            result['text'] = _pace_text(parsed['pace'], None, effects)
        else:
            leg_row = leg(parsed['pace'], hours)
            travel.update(hours=hours, miles=leg_row['miles'])
            result['miles'] = leg_row['miles']
            result['text'] = _pace_text(parsed['pace'], hours, effects, leg_row['miles'])
        if forced:
            march = forced_march(hero, hours, state=state)
            state['hero'] = hero.model_dump()
            result['forced_march'] = dict(exhaustion=march['exhaustion'],
                                          extra_hours=march['extra_hours'],
                                          slots=[dict(hour=slot['hour'], dc=slot['dc'],
                                                      total=slot['save']['total'], failed=slot['failed'])
                                                 for slot in march['slots']])
            result['text'] += ' ' + _march_text(march)
        return [result]
    return transition


SKILL_RU = {'perception': 'Внимательность', 'survival': 'Выживание', 'stealth': 'Скрытность'}
ABILITY_RU = {'wisdom': 'Мудрость', 'dexterity': 'Ловкость'}
PACE_RU = {'fast': 'Быстрый', 'normal': 'Обычный', 'slow': 'Медленный'}


def _miles(count):
    """Русское число миль: 1 миля, 2 мили, 5 миль."""
    tail = int(count) % 100
    if 11 <= tail <= 14:
        return '%s миль' % count
    return {1: '%s миля', 2: '%s мили', 3: '%s мили', 4: '%s мили'}.get(tail % 10, '%s миль') % count


def _effects_text(effects):
    """«Помеха на Мудрость (Выживание и Внимательность), Ловкость (Скрытность)»."""
    if not effects:
        return 'Эффектов на проверки у этого темпа нет.'
    parts = []
    for mode in ('disadvantage', 'advantage'):
        chosen = [item for item in effects if item['mode'] == mode]
        if not chosen:
            continue
        by_ability = {}
        for item in chosen:
            by_ability.setdefault(item['ability'], []).append(SKILL_RU[item['skill']])
        parts.append('%s на %s.' % ('Помеха' if mode == 'disadvantage' else 'Преимущество',
                                    ', '.join('%s (%s)' % (ABILITY_RU[ability], ' и '.join(skills))
                                              for ability, skills in by_ability.items())))
    return ' '.join(parts)


def _pace_text(pace, hours, effects, miles=None):
    row = pace_table()[pace]
    if hours is None:
        return '%s темп: %s в час, %s в день. %s' % (
            PACE_RU[pace], _miles(row['miles_per_hour']), _miles(row['miles_per_day']),
            _effects_text(effects))
    return '%s темп: %s в час, %s в день; за %s ч — %s. %s' % (
        PACE_RU[pace], _miles(row['miles_per_hour']), _miles(row['miles_per_day']), hours,
        _miles(miles), _effects_text(effects))


def _march_text(march):
    if not march['slots']:
        return 'Часов сверх дня нет — форсированного марша не было.'
    parts = ['час %s: спасбросок Телосложения Сл %s — %s' % (
        slot['hour'], slot['dc'], 'провал' if slot['failed'] else 'успех') for slot in march['slots']]
    return 'Форсированный марш: ' + '; '.join(parts) + '. Истощение: %s.' % march['exhaustion']


def _cover_name(cover):
    return {'three_quarters': '3/4', 'total': 'полное', 'heavy_obscurement': 'сильное заслонение',
            'half': '1/2', 'none': 'нет', 'named': 'заявленное место'}[cover]


def _hide_transition(cover, out_of_sight=True):
    def transition(state):
        from .contracts import Hero
        pace = (state.get('travel') or {}).get('pace')
        # Сначала условия Засады: отказ «укрытие не объявлено» не должен
        # зависеть от того, что ещё есть в состоянии.
        conditions = hide_conditions(cover=cover, out_of_sight=out_of_sight, pace=pace)
        if not conditions['allowed']:
            raise ValueError(conditions['reason'])
        hero = Hero.model_validate(state['hero'])
        result = hide_roll(hero, cover=cover, out_of_sight=out_of_sight, pace=pace, state=state)
        check = result['check']
        state.setdefault('travel', {})['hidden'] = result['hidden']
        return [dict(type='scripted', kind='hide', cover=cover, hidden=result['hidden'],
                     dc=result['conditions']['dc'], total=check['total'],
                     hiding_dc=result['hiding_dc'],
                     text='Засада: укрытие %s, Ловкость (Скрытность) d20 %+d = %s против Сл %s — %s. %s'
                          % (_cover_name(cover), check['bonus'], check['total'],
                             result['conditions']['dc'],
                             'герой спрятан' if result['hidden'] else 'не спрятался',
                             ('Его Сл поиска: %s.' % result['hiding_dc']) if result['hidden'] else ''))]
    return transition


def _obscurement_transition():
    def transition(state):
        heavy = obscurement('heavy')
        return [dict(type='scripted', kind='obscurement', level='heavy', vision=heavy['vision'],
                     see_into=heavy['see_into'], hide_allowed=heavy['hide_allowed'],
                     text='Сильно заслонённая область: видеть внутрь нельзя — смотрящий получает '
                          'состояние «ослеплённый» (глава 1, «Заслонённые области»); прятаться '
                          'в ней можно.')]
    return transition


def _search_transition(said):
    def transition(state):
        from .contracts import Hero
        area = search_area(declared_area(said))
        if not area['allowed']:
            raise ValueError(area['reason'])
        hero = Hero.model_validate(state['hero'])
        pace = (state.get('travel') or {}).get('pace')
        result = search(hero, declared_area(said), pace=pace, state=state)
        check = result['check']
        return [dict(type='scripted', kind='search', area=result['area']['declared'],
                     total=check['total'], ability=result['area']['ability'],
                     text='Поиск (%s): проверка Мудрости (Восприятие) d20 %+d = %s. Сл задаёт сцена; '
                          'в главе 1 числа нет.' % (result['area']['declared'], check['bonus'],
                                                    check['total']))]
    return transition


def _load_transition(weight, mode):
    def transition(state):
        from .contracts import Hero
        hero = Hero.model_validate(state['hero'])
        limits = load(hero, weight, mode=mode)
        return [dict(type='scripted', kind='load', weight_lb=weight, mode=mode,
                     carry_lb=limits['carry_lb'], drag_lb=limits['drag_lb'],
                     speed_ft=limits['speed_ft'],
                     text='Груз: %s фунтов; грузоподъёмность %s (%s × %s, Сила %s), '
                          'тащить/толкать до %s. %s.'
                          % (weight, limits['carry_lb'], limits['multiplier'], limits['size'],
                             limits['strength'], limits['drag_lb'], limits['note']))]
    return transition


def _mounted_transition(hours):
    def transition(state):
        travel = state.setdefault('travel', {})
        pace = travel.get('pace', 'normal')
        result = mounted_leg(pace, hours, used_hours=travel.get('mount_hours', 0))
        travel['mount_hours'] = result['mount_hours_after']
        return [dict(type='scripted', kind='mounted', hours=result['hours'], miles=result['miles'],
                     double_hours=result['double_hours'], mounted=True,
                     text='Верхом: %s ч, из них удвоено %s ч, всего %s миль. После часа удвоения '
                          'скакуну нужен Короткий или Долгий отдых.'
                          % (result['hours'], result['double_hours'], result['miles']))]
    return transition


def _vehicle_transition(kind, hours):
    def transition(state):
        data = vehicle(kind, hours=hours)
        state.setdefault('travel', {})['vehicle'] = kind
        if kind == 'water':
            return [dict(type='scripted', kind='vehicle', kind_of='water', hours=data['hours'],
                         text='Судно: темп не выбирают, скорость задаёт судно; до %s часов в сутки '
                              '(%s).' % (data['max_hours'], ', '.join(data['depends_on'])))]
        return [dict(type='scripted', kind='vehicle', kind_of='land', pace=data['pace'],
                     hours=data['hours'], miles=data['miles'],
                     text='Наземный транспорт: обычный темп, за %s ч — %s миль.%s'
                          % (data['hours'], data['miles'],
                             '' if hours <= data['hours'] else
                             ' Часы сверх дня — уже форсированный марш возницы, и он считается '
                             'отдельно: здесь только %s ч.' % data['hours']))]
    return transition


def _destroy_transition(thing):
    def transition(state):
        data = destroy(thing)
        return [dict(type='scripted', kind='destroy', object=data['object'],
                     text='Объект уничтожен действием: %s.' % data['note'])]
    return transition


def _adjudication_transition(topic, reader):
    def transition(state):
        data = reader()
        return [dict(type='scripted', kind='adjudication', topic=topic, data=data,
                     text='%s: %s' % (topic, data['note']))]
    return transition


#: Имена обработчиков из `readiness.handler` — прямые ссылки на функции модуля,
#: чтобы «есть обработчик» можно было проверить вызовом, а не только строкой.
travel_pace = leg
mounted_travel = mounted_leg
vehicles = vehicle
hiding = hide_roll
destroy_object = destroy
