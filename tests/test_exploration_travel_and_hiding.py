"""r176: дорога, укрытия, поиск и груз считаются, а не описываются словами.

Карточка r176 требует одиннадцать правил главы «Исследование» вытащить из
`blocked`. Здесь по каждому правилу три вещи:

1. **счёт**: числа берутся из `facts` корпуса (темп, эффекты, верхом, транспорт,
   засада, поиск, заслонённость) и из страниц, на которые глава отправляет
   (грузоподъёмность — SRD 5.2.1 с. 178, Сл Засады — с. 183, форсированный
   марш — DMG 2024 с. 34);
2. **отказ**: нет укрытия, область поиска не объявлена или объект вне её,
   перегруз, второе удвоение без отдыха, магический или прочный объект —
   `ValueError` с причиной, а не молчаливый ноль;
3. **путь игрока**: реплика «идём быстрым темпом до вечера» доходит через
   `Game.play` до `Store.record_scenario`, и в событии видно посчитанное.

Кости всегда бросает движок (`RulesetEngine`); хост считает таблицы и условия.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOKS = ROOT / 'runtime' / 'books'
CHAPTER = ROOT / 'rules_data' / 'v2' / 'chapters' / 'phb-01' / '03-exploration.rules.json'

from solo.app import PlayRequest  # noqa: E402
from solo.contracts import Hero  # noqa: E402
from solo.exploration import (SOURCES, carrying, destroy, forced_march,  # noqa: E402
                              forced_march_slots, hide_conditions, hide_roll, host_intent, leg,
                              load, mounted_leg, obscurement, pace_effects, pace_table,
                              search, search_area, vehicle)
from solo.game import Game  # noqa: E402
from solo.llm import MockLLM  # noqa: E402
from solo.rulesets import BUILTINS, RulesetEngine  # noqa: E402
from solo.storage import Store  # noqa: E402
from solo import travel  # noqa: E402


class Dice:
    """Детерминированная к20: тест не зависит от системного генератора."""

    def __init__(self, *values):
        self.values = iter(values)
        self.calls = 0

    def randint(self, low, high):
        value = next(self.values)
        assert low <= value <= high
        self.calls += 1
        return value


ABILITIES = dict(strength=15, dexterity=14, constitution=14, intelligence=10, wisdom=12, charisma=10)


def hero(**extra):
    return Hero(name='Путник', abilities=ABILITIES, **extra)


def campaign(tmp_path):
    store = Store(tmp_path / 'exploration.sqlite3')
    return store, store.create('Дорога', hero(), BUILTINS['dnd2024'])['id']


def corpus():
    document = json.loads(CHAPTER.read_text(encoding='utf-8'))
    return {row['id'].rsplit('.', 1)[-1]: row for row in document['rules']}


ELEVEN = ('travel_pace', 'mounted_travel', 'vehicles', 'hiding', 'hidden_objects',
          'heavy_obscurement', 'carrying_objects', 'exploration_equipment',
          'special_senses', 'destroy_fragile_object', 'hazards')
VERIFIED = ('travel_pace', 'mounted_travel', 'hiding', 'hidden_objects',
            'heavy_obscurement', 'carrying_objects', 'destroy_fragile_object')
ADJUDICATED = ('vehicles', 'exploration_equipment', 'special_senses', 'hazards')


# ------------------------------------------------------------------ темп

def test_pace_table_comes_from_the_book_facts_not_from_arithmetic():
    table = pace_table()
    assert [table[pace]['feet_per_minute'] for pace in ('fast', 'normal', 'slow')] == [400, 300, 200]
    assert [table[pace]['miles_per_hour'] for pace in ('fast', 'normal', 'slow')] == [4, 3, 2]
    assert [table[pace]['miles_per_day'] for pace in ('fast', 'normal', 'slow')] == [30, 24, 18]
    # Приложение держит те же футы в минуту в пресете правил: один источник.
    assert BUILTINS['dnd2024'].travel_pace_ft == {pace: row['feet_per_minute']
                                                  for pace, row in table.items()}


def test_day_column_is_not_recomputed_from_hours():
    # Медленный темп: 2 мили × 8 часов = 16, а книга говорит 18 — столбец дня отдельный.
    assert leg('slow', 8)['miles'] == 18
    assert leg('fast', 8)['miles'] == 30
    assert leg('normal', 8)['miles'] == 24
    assert leg('slow', 7)['miles'] == 14          # часть дня — по столбцу «Час»
    assert leg('fast', 10)['miles'] == 38         # часы сверх дня добавляются часовыми милями


def test_fast_covers_more_and_costs_checks_slow_is_the_reverse():
    assert leg('fast', 6)['miles'] > leg('normal', 6)['miles'] > leg('slow', 6)['miles']
    fast = {(item['skill'], item['mode']) for item in pace_effects('fast')}
    assert fast == {('survival', 'disadvantage'), ('perception', 'disadvantage'),
                    ('stealth', 'disadvantage')}
    assert {(item['skill'], item['mode']) for item in pace_effects('normal')} == {('stealth', 'disadvantage')}
    slow = {(item['skill'], item['mode']) for item in pace_effects('slow')}
    assert slow == {('survival', 'advantage'), ('perception', 'advantage')}
    assert pace_effects('fast')[0]['ability'] == 'wisdom'
    assert next(item for item in pace_effects('fast') if item['skill'] == 'stealth')['ability'] == 'dexterity'


def test_pace_effects_reach_the_die():
    engine = RulesetEngine(BUILTINS['dnd2024'])
    disadvantage = engine.skill(hero(), 'stealth', 10, rng=Dice(15, 4), disadvantage=True)
    assert disadvantage['mode'] == 'помеха' and disadvantage['selected'] == 4
    assert disadvantage['total'] == 4 + 2


# ---------------------------------------------------- форсированный марш

def test_forced_march_saves_use_the_dmg_dcs_and_apply_exhaustion():
    assert forced_march_slots(10) == [dict(hour=9, dc=11), dict(hour=10, dc=12)]
    assert forced_march_slots(8) == []
    walker = hero()
    result = forced_march(walker, 10, rng=Dice(5, 18))
    assert [slot['failed'] for slot in result['slots']] == [True, False]
    assert result['slots'][0]['save']['dc'] == 11 and result['slots'][1]['save']['dc'] == 12
    assert walker.exhaustion == 1                     # один провал — одна степень
    assert SOURCES['forced_march']['exhaustion_per_failure'] == 1


def test_forced_march_does_not_silently_kill_by_exhaustion():
    walker = hero(exhaustion=5)
    with pytest.raises(ValueError, match='смерть'):
        forced_march(walker, 9, rng=Dice(1))
    assert walker.exhaustion == 5                     # последствие не применено молча


def test_forced_march_is_counted_only_past_the_eight_hour_day():
    assert forced_march(hero(), 8, rng=Dice())['slots'] == []


def test_forced_march_without_hours_is_refused_rather_than_guessed(tmp_path):
    store, cid = campaign(tmp_path)
    game = Game(store, MockLLM())
    for request_id, text, reason in (
            ('march-short', 'идём быстрым темпом форсированным маршем', 'числа часов'),
            ('march-eight', 'идём быстрым темпом форсированным маршем 8 часов', 'после 8 часов')):
        answer = asyncio.run(game.play(cid, PlayRequest(request_id=request_id, text=text)))
        assert answer['kind'] == 'reply' and reason in answer['reply'], (text, answer)


def test_pace_without_a_duration_does_not_invent_a_full_day(tmp_path):
    store, cid = campaign(tmp_path)
    game = Game(store, MockLLM())
    answer = asyncio.run(game.play(cid, PlayRequest(request_id='pace-bare', text='идём медленным темпом')))
    result = answer['event']['results'][0]
    assert result['hours'] is None and 'miles' not in result
    assert '2 мили в час' in result['text'] and '18 миль в день' in result['text']
    travel_state = store.campaign(cid)['state']['travel']
    assert travel_state['pace'] == 'slow' and 'miles' not in travel_state


# ----------------------------------------------------------------- верхом

def test_mount_doubles_one_hour_then_needs_a_rest():
    doubled = mounted_leg('normal', 1)
    assert doubled['miles'] == 6 and doubled['double_hours'] == 1      # 3 мили + час
    assert mounted_leg('normal', 3)['miles'] == 12                     # 9 миль дня + час
    # Полный день верхом — столбец дня плюс один час, а не пересчёт дня:
    assert mounted_leg('fast', 8)['miles'] == 34                       # 30 + 4
    assert mounted_leg('slow', 8)['miles'] == 20                       # 18 + 2
    with pytest.raises(ValueError, match='отдых'):
        mounted_leg('normal', 2, used_hours=1)
    again = mounted_leg('normal', 1, used_hours=1)
    assert again['double_hours'] == 0 and again['miles'] == 3          # без удвоения, но идти можно


def test_rest_gives_the_doubling_hour_back(tmp_path):
    from solo.rules_api import apply_rest
    store, cid = campaign(tmp_path)
    game = Game(store, MockLLM())
    answer = asyncio.run(game.play(cid, PlayRequest(request_id='ride-1', text='еду верхом 1 час')))
    assert answer['event']['results'][0]['kind'] == 'mounted'
    assert store.campaign(cid)['state']['travel']['mount_hours'] == 1
    # Короткий отдых — законный сброс счётчика удвоения (facts mounted_travel.refresh).
    event = apply_rest(store, cid, 'rest-1', 'short', text='короткий отдых')
    assert event['results'][0]['kind'] == 'rest'
    assert store.campaign(cid)['state']['travel']['mount_hours'] == 0
    assert asyncio.run(game.play(cid, PlayRequest(request_id='ride-2',
                                                  text='еду верхом 2 часа')))['kind'] == 'event'


# ------------------------------------------------------------ укрытия

def test_cannot_hide_without_cover_and_half_cover_is_not_enough():
    empty = hide_conditions()
    assert empty['allowed'] is False and 'укрытие' in empty['reason']
    half = hide_conditions(cover='half')
    assert half['allowed'] is False and '3/4' in half['reason']
    assert hide_conditions(cover='three_quarters')['allowed'] is True
    assert hide_conditions(cover='total')['allowed'] is True
    assert hide_conditions(heavy_obscurement=True)['allowed'] is True


def test_hiding_needs_to_be_out_of_sight():
    seen = hide_conditions(cover='three_quarters', out_of_sight=False)
    assert seen['allowed'] is False and 'линии обзора' in seen['reason']


def test_hide_check_is_dc_15_dexterity_stealth_and_its_total_is_the_search_dc():
    hunter = hero()
    result = hide_roll(hunter, cover='three_quarters', rng=Dice(15))
    check = result['check']
    assert check['dc'] == 15 and check['skill'] == 'stealth'
    assert check['total'] == 15 + 2                   # Ловкость 14 и владение не заявлено
    assert result['hidden'] is True and result['hiding_dc'] == 17
    assert 'шум громче шёпота' in result['conditions']['ends_on']


def test_pace_penalty_reaches_the_hide_check():
    fast = hide_conditions(cover='three_quarters', pace='fast')
    assert fast['disadvantage'] is True
    slow = hide_conditions(cover='three_quarters', pace='slow')
    assert slow['disadvantage'] is False
    result = hide_roll(hero(), cover='three_quarters', pace='fast', rng=Dice(18, 7))
    assert result['check']['mode'] == 'помеха' and result['check']['selected'] == 7


# ------------------------------------------------------- заслонённая область

def test_heavy_obscurement_blocks_vision_and_allows_hiding():
    heavy = obscurement('heavy')
    assert heavy['vision'] == 'blocked' and heavy['see_into'] == 'blinded'
    assert heavy['hide_allowed'] is True
    assert obscurement('light')['hide_allowed'] is False
    with pytest.raises(ValueError, match='заслонённость'):
        obscurement('тьма кромешная')


# ------------------------------------------------------------------ поиск

def test_search_outside_the_declared_area_is_refused_with_a_reason():
    undeclared = search_area('')
    assert undeclared['allowed'] is False and 'не объявлена' in undeclared['reason']
    for vague in ('везде', 'всюду', 'вокруг'):
        assert search_area(vague)['allowed'] is False
    outside = search_area('в сундуке', hidden='за камином')
    assert outside['allowed'] is False and 'не обнаружит' in outside['reason']


def test_search_area_is_compared_by_stem_not_by_raw_string():
    assert search_area('в сундуке', hidden='сундук')['allowed'] is True
    assert search_area('за камином', hidden='камин')['allowed'] is True
    assert search_area('в сундуке', hidden='за камином')['allowed'] is False


def test_search_inside_the_declared_area_rolls_wisdom_perception():
    seeker = hero(skill_proficiencies=['perception'])
    result = search(seeker, 'за камином', dc=12, rng=Dice(9))
    check = result['check']
    assert check['skill'] == 'perception' and check['ability'] == 'wisdom'
    assert check['total'] == 9 + 1 + 2 and result['success'] is True
    assert result['finds'] == 'object_or_details_or_both'
    miss = search(seeker, 'за камином', dc=20, rng=Dice(3))
    assert miss['success'] is False and miss['finds'] is None


# -------------------------------------------------------------------- груз

def test_carrying_capacity_uses_size_and_strength_from_the_glossary_page():
    walker = hero()                                   # Сила 15, Medium
    limits = carrying(walker)
    assert (limits['carry_lb'], limits['drag_lb']) == (225, 450)
    assert carrying(Hero(name='Кроха', size='Small', abilities=ABILITIES))['carry_lb'] == 225
    assert carrying(Hero(name='Громила', size='Large', abilities=ABILITIES))['carry_lb'] == 450


def test_overload_is_refused_with_the_exact_numbers():
    with pytest.raises(ValueError, match='225'):
        load(hero(), 226)
    with pytest.raises(ValueError, match='450'):
        load(hero(), 451, mode='drag')
    assert load(hero(), 225)['allowed'] is True
    dragged = load(hero(), 400, mode='drag')
    assert dragged['allowed'] is True and dragged['speed_ft'] == 5


# ------------------------------------------------- разрушение объектов

def test_fragile_nonmagical_object_breaks_and_others_are_refused():
    assert destroy('стеклянную бутылку')['automatic'] is True
    assert destroy('лист бумаги')['action_cost'] == 1
    with pytest.raises(ValueError, match='Магический'):
        destroy('магический амулет')
    with pytest.raises(ValueError, match='Прочный'):
        destroy('каменную стену')
    # Дверь — прочный объект из примеров книги; хрупкость не додумывается.
    with pytest.raises(ValueError, match='Прочность не подтверждена'):
        destroy('дверь')


# ------------------------------------------------------------ транспорт

def test_land_vehicle_keeps_normal_pace_and_water_has_no_pace_choice():
    land = vehicle('wagon')
    assert land['pace'] == 'normal' and land['miles'] == 24 and land['pace_choice'] is True
    water = vehicle('boat', hours=30)
    assert water['pace_choice'] is False
    assert water['hours'] == 24 and water['max_hours'] == 24
    assert water['depends_on'] == ['vessel_type', 'crew']


# ------------------------------------------ правила, оставленные Мастеру

def test_adjudicated_rules_keep_their_reason():
    from solo.exploration import exploration_equipment, hazards, special_senses
    senses = special_senses()
    assert senses['senses'] == ['blindsight', 'darkvision', 'tremorsense', 'truesight']
    assert senses['ranges_known'] is False and 'существа' in senses['note']
    threat = hazards()
    assert threat['hazards'] == ['burning', 'dehydration', 'suffocation', 'malnutrition', 'falling']
    assert threat['numbers_known'] is False
    gear = exploration_equipment()
    assert gear['adjudication_required'] is True and gear['rules_chapter'] == 6


# ------------------------------------------------------------------ корпус

def test_every_handler_name_resolves_to_a_real_function():
    from solo import exploration
    rules = corpus()
    names = {rules[key]['readiness']['handler'] for key in ELEVEN
             if rules[key]['readiness']['handler']}
    assert names == {'exploration.travel_pace', 'exploration.mounted_travel', 'exploration.vehicles',
                     'exploration.hiding', 'exploration.search', 'exploration.carrying',
                     'exploration.obscurement', 'exploration.destroy_object'}
    for name in names:
        function = getattr(exploration, name.split('.', 1)[1], None)
        assert callable(function), name


def test_eleven_rules_stopped_being_blocked_and_each_names_its_handler():
    rules = corpus()
    statuses = {key: rules[key]['readiness']['status'] for key in ELEVEN}
    assert 'blocked' not in statuses.values()
    assert [key for key in ELEVEN if statuses[key] == 'engine_subset_verified'] == list(VERIFIED)
    assert [key for key in ELEVEN if statuses[key] == 'adjudication_required'] == list(ADJUDICATED)
    handlers = {key: rules[key]['readiness']['handler'] for key in ELEVEN}
    assert handlers['travel_pace'] == 'exploration.travel_pace'
    assert handlers['carrying_objects'] == 'exploration.carrying'
    assert all(handlers[key] is None for key in ('special_senses', 'hazards', 'exploration_equipment'))
    for key in VERIFIED:
        assert not rules[key]['readiness']['blockers'], key
    for key in ADJUDICATED:
        assert rules[key]['readiness']['blockers'], key
    assert all(rules[key]['revision'] == 2 for key in ELEVEN)


def test_corpus_still_validates_against_the_routing_schema():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    folder = ROOT / 'rules_data' / 'v2'
    registry = Registry()
    for path in folder.glob('*.schema.json'):
        registry = registry.with_resource(path.name, Resource.from_contents(
            json.loads(path.read_text(encoding='utf-8')), default_specification=DRAFT202012))
    schema = json.loads((folder / 'routing.schema.json').read_text(encoding='utf-8'))
    document = json.loads(CHAPTER.read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(schema, registry=registry).iter_errors(document))
    assert not errors, [list(error.absolute_path) + [error.message] for error in errors[:3]]


# -------------------------------------------------------------- живой путь

def test_player_phrase_sets_the_pace_and_returns_the_numbers(tmp_path):
    store, cid = campaign(tmp_path)
    game = Game(store, MockLLM())
    answer = asyncio.run(game.play(cid, PlayRequest(request_id='pace-1',
                                                    text='идём быстрым темпом до вечера')))
    assert answer['kind'] == 'event', answer
    result = answer['event']['results'][0]
    assert result['kind'] == 'travel_pace'
    assert (result['pace'], result['hours'], result['miles']) == ('fast', 8, 30)
    assert result['miles_per_hour'] == 4 and result['feet_per_minute'] == 400
    assert {item['skill'] for item in result['effects']} == {'survival', 'perception', 'stealth'}
    assert 'Помеха' in result['text'] and '30' in result['text']
    state = store.campaign(cid)['state']
    assert state['travel']['pace'] == 'fast' and state['travel']['miles'] == 30
    again = asyncio.run(game.play(cid, PlayRequest(request_id='pace-1',
                                                  text='идём быстрым темпом до вечера')))
    assert again['event']['id'] == answer['event']['id']          # повтор не бросает заново


def test_forced_march_phrase_rolls_saves_and_raises_exhaustion_level(tmp_path):
    store, cid = campaign(tmp_path)
    game = Game(store, MockLLM())
    answer = asyncio.run(game.play(cid, PlayRequest(request_id='march-1',
                                                    text='идём быстрым темпом форсированным маршем 10 часов')))
    result = answer['event']['results'][0]
    assert result['kind'] == 'travel_pace' and result['hours'] == 10
    assert result['miles'] == 38                                   # 30 за день + 2 часа × 4 мили
    march = result['forced_march']
    assert [slot['dc'] for slot in march['slots']] == [11, 12]
    assert 'Форсированный марш' in result['text']
    assert store.campaign(cid)['state']['hero']['exhaustion'] == march['exhaustion']


def test_obscurement_and_room_search_reach_the_host(tmp_path):
    store, cid = campaign(tmp_path)
    game = Game(store, MockLLM())
    answer = asyncio.run(game.play(cid, PlayRequest(request_id='fog-1', text='вхожу в туман')))
    result = answer['event']['results'][0]
    assert result['kind'] == 'obscurement' and result['see_into'] == 'blinded'
    answer = asyncio.run(game.play(cid, PlayRequest(request_id='room-1', text='осматриваю комнату')))
    assert answer['event']['results'][0]['kind'] == 'search'
    assert answer['event']['results'][0]['area'] == 'комнату'


def test_refusals_reach_the_player_as_a_notice(tmp_path):
    store, cid = campaign(tmp_path)
    game = Game(store, MockLLM())
    for request_id, text, reason in (('hide-1', 'прячусь', 'укрытие'),
                                     ('search-1', 'ищу ловушку', 'не объявлена'),
                                     ('load-1', 'беру груз 500 фунтов', 'Перегруз')):
        answer = asyncio.run(game.play(cid, PlayRequest(request_id=request_id, text=text)))
        assert answer['kind'] == 'reply', (text, answer)
        assert reason in answer['reply'], (text, answer['reply'])


def test_hide_and_search_and_load_and_vehicle_reach_the_host(tmp_path):
    store, cid = campaign(tmp_path)
    game = Game(store, MockLLM())
    cases = (('прячусь за бочкой', 'hide'), ('ищу ловушку в сундуке', 'search'),
             ('беру груз 200 фунтов', 'load'), ('еду верхом 2 часа', 'mounted'),
             ('плыву на лодке', 'vehicle'), ('ломаю стеклянную бутылку', 'destroy'))
    for index, (text, kind) in enumerate(cases):
        answer = asyncio.run(game.play(cid, PlayRequest(request_id='case-%d' % index, text=text)))
        assert answer['kind'] == 'event', (text, answer)
        assert answer['event']['results'][0]['kind'] == kind, (text, answer['event']['results'])


def test_declared_pace_changes_the_road_duration():
    edge = dict(meters=2000, minutes=30, timing='distance')
    state = dict(hero={'speed_ft': 30}, ruleset=BUILTINS['dnd2024'].model_dump(),
                 travel={'pace': 'fast'})
    fast = travel.duration(edge, 30, state)
    state['travel']['pace'] = 'slow'
    slow = travel.duration(edge, 30, state)
    assert fast == 17 and slow == 33
    assert slow > fast


def test_intent_only_answers_its_own_phrases():
    assert host_intent({}, 'здравствуй, трактирщик') is None
    assert host_intent({}, 'идём быстрым темпом до вечера')[0] == 'Темп путешествия'
    assert host_intent({}, 'прячусь за бочкой')[0] == 'Засада'
    # Обычные действия остаются прежнему обработчику: широкое «ищу», упоминание
    # лодки в осмотре, «дорогая вещь» или «рвусь в бой» — не механический ход.
    for text in ('ищу травы', 'вижу лодку на реке', 'смотрю на лошадей', 'иду в таверну',
                 'эта дорогая вещь выглядит обычной', 'рвусь в бой'):
        assert host_intent({}, text) is None, text
    # А «в комнате» и «за травинкой» — не укрытие: заявка есть, качества нет,
    # поэтому переход отказывает, а не выдаёт укрытие 3/4.
    for text, expected in (('прячусь в комнате', 'укрытием не считается'),
                           ('прячусь за травинкой', 'укрытием не считается'),
                           ('прячусь', 'укрытие не объявлено')):
        label, transition = host_intent({}, text)
        assert label == 'Засада'
        with pytest.raises(ValueError, match=expected):
            transition({})
    # В бою ходом владеет движок: хост свой переход не подставляет.
    assert host_intent({'combat': {'active': True}}, 'идём быстрым темпом') is None


def test_examine_of_registered_object_is_not_a_search():
    """«Осматриваю дверь» — описание объекта, а не бросок Восприятия.

    Хостовый поиск (`host_intent`) ловит только скрытое: тайники, ловушки,
    обыск. Зарегистрированный объект сцены ведёт `interactions`, иначе игрок
    видит «Поиск (дверь): проверка Мудрости…» вместо описания предмета,
    а событие приходит без `action`.
    """
    from solo.interactions import SceneObject
    state = {'location': 'Комната', 'locations': [{'name': 'Комната', 'npc': [], 'interactions': [
        SceneObject(id='door', name='дверь', kind='door', lock='unlocked', destination='Кладовая',
                    description='Деревянная дверь без защёлки.').model_dump()]}]}
    assert host_intent(state, 'Осматриваю дверь') is None
    assert host_intent(state, 'изучаю дверь') is None
    # Скрытое остаётся поиском: явная заявка на тайник — прочёсывание.
    assert host_intent(state, 'осматриваю дверь в поисках тайника')[0] == 'Поиск'
    assert host_intent(state, 'обыскиваю дверь')[0] == 'Поиск'
    # Осмотр места объектом не является — прежний маршрут поиска комнаты цел.
    assert host_intent(state, 'осматриваю комнату')[0] == 'Поиск'


# ---------------------------------------------------- сверка с книгой

def _page(name, number):
    from pypdf import PdfReader
    return (PdfReader(BOOKS / name).pages[number - 1].extract_text() or '').replace('\uFFFE', '')


@pytest.mark.skipif(not (BOOKS / 'SRD_CC_v5.2.1.pdf').is_file(),
                    reason='SRD 5.2.1 нет на диске: сверка страниц недоступна')
def test_srd_pages_carry_the_numbers_used_for_carrying_hiding_and_search():
    capacity = _page('SRD_CC_v5.2.1.pdf', SOURCES['carrying']['pdf_page'])
    assert 'Carrying Capacity' in capacity
    for token in ('Str. × 7.5 lb.', 'Str. × 15 lb.', 'Str. × 30 lb.', 'Str. × 60 lb.', 'Str. × 120 lb.'):
        assert token in capacity, token
    hide = ' '.join(_page('SRD_CC_v5.2.1.pdf', SOURCES['hiding']['pdf_page']).split())
    assert 'DC 15 Dexterity (Stealth) check' in hide
    assert 'Heavily Obscured or behind Three-Quarters Cover or Total Cover' in hide
    seeking = ' '.join(_page('SRD_CC_v5.2.1.pdf', SOURCES['search']['pdf_page']).split())
    assert 'Perception' in seeking and 'Concealed creature or object' in seeking


@pytest.mark.skipif(not (BOOKS / 'Руководство Мастера 2024 v1.1.pdf').is_file(),
                    reason='Руководство Мастера 2024 нет на диске: сверка страницы недоступна')
def test_dmg_page_34_carries_the_forced_march_rule():
    page = ' '.join(_page('Руководство Мастера 2024 v1.1.pdf',
                          SOURCES['forced_march']['pdf_page']).split())
    assert 'более 8 часов' in page
    assert '10 + 1' in page
    assert 'степень истощения' in page
    assert 'спасброске Телосложения' in page
