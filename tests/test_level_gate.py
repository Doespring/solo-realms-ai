"""Сверка уровня героя с книгой на старте кампании (L02).

Сценарий владельца (08.11): книга рассчитана на 8-й уровень, герой 1-го.
Мастер в первой реплике называет оба числа и предлагает пересборку — до
первого броска. «Пересобрать» открывает редактор на книжном уровне, и старого
героя в кампании не остаётся. «Играем так» фиксирует `balance_override`
и считает опасность первого боя от уровня героя, а не от книги.

Числа берутся из закреплённого датасета монстров (CR) и из движка (лист
героя), а не из головы теста.
"""
import os
os.environ['SOLO_TEST'] = '1'

import pytest
from fastapi.testclient import TestClient

from solo import level_gate
from solo.app import create_app
from solo.game_commands import GameCommand as SoloCommand
from solo.llm import MockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_wizard import suggested_draft
from solo.storage import Store

FIGHTER_SCORES = dict(strength=15, dexterity=14, constitution=13,
                      intelligence=12, wisdom=10, charisma=8)
KOBOLD = 'kobold-warrior'
# Книжный отряд: восемь кобольдов на четырёх героев 1-го уровня (первый эпизод
# Hoard, стр. 10). Для одиночки 1-го уровня это заведомо смертельный бой.
BOOK_WARBAND = 8


def hero_build(level=1):
    draft, state = suggested_draft(class_slug='fighter', species_slug='human',
                                   background_slug='soldier', level=level,
                                   ability_scores=FIGHTER_SCORES)
    assert state['ready'], state['issues']
    return state['build']


def warband(count=BOOK_WARBAND):
    return dict(grid=dict(width=14, height=10, blocked_cells=[]),
                hero_position=dict(x=1, y=1, z=0),
                actors=[dict(id='kobold-' + str(index), monster=KOBOLD,
                             position=dict(x=3 + index, y=1, z=0))
                        for index in range(count)])


def book_campaign(tmp_path, *, hero_level=1, book_minimum=8, book_maximum=8, kobolds=BOOK_WARBAND):
    """Кампания по книге: герой собран на своём уровне, книга просит свой."""
    api = Nat20GameEngine(Store(tmp_path / 'level-gate.sqlite3'))
    campaign = api.create_experimental_campaign(
        'Тест Тиамат', build=hero_build(hero_level),
        encounters={'first-encounter': warband(kobolds)}, seed=7)['id']

    def install(state):
        level_gate.install_requirement(state, book='Тиамат', minimum=book_minimum,
                                       maximum=book_maximum, party=4)
        return [dict(type='scripted', kind='level_gate',
                     text='Книга «Тиамат» рассчитана на ' + str(book_minimum) + '-й уровень.')]

    api.store.record_scenario(campaign, 'book-requirement', 'Требование книги', install)
    return api, campaign


def first_fight(api, campaign):
    current = api.store.campaign(campaign)
    return current['state']['nat20']['definition']['encounters']['first-encounter']


def test_the_master_names_both_numbers_before_the_first_roll(tmp_path):
    api, campaign = book_campaign(tmp_path)
    gate = api.level_gate(campaign)
    assert gate['gap'] == 'lower'
    assert gate['required'] == 8 and gate['hero_level'] == 1
    assert '8-й уровень' in gate['ask'] and 'твой герой 1-го' in gate['ask'], gate['ask']
    assert [option['id'] for option in gate['options']] == ['rebuild', 'accept']
    assert not gate['decided'] and gate['override'] is None

    # До ответа бой не открывается: отказ — это вопрос Мастера, а не бросок.
    current = api.store.campaign(campaign)
    command = SoloCommand(request_id='gate-probe', expected_version=current['version'],
                          action=dict(kind='start_combat', encounter_id='first-encounter'))
    with pytest.raises(ValueError) as refusal:
        api.execute(campaign, command)
    assert gate['ask'] in str(refusal.value)
    # Кампания не сдвинулась: ни хода, ни боя.
    assert api.store.campaign(campaign)['version'] == current['version']


def test_playing_as_is_records_the_override_and_scales_the_first_fight(tmp_path):
    api, campaign = book_campaign(tmp_path)
    before_turn = api.store.campaign(campaign)['state'].get('turn', 0)
    result = api.accept_player_level(campaign, request_id='accept-1')
    current = api.store.campaign(campaign)
    state = current['state']
    assert state['balance_override'] == 'player_level', 'решение игрока живёт в кампании'
    assert state['level_gate']['decision'] == 'player_level'
    # Журнальное событие — не ход героя: история ещё не начата.
    assert state.get('turn', 0) == before_turn
    # Числа первого боя — от героя, а не от книги.
    actors = first_fight(api, campaign)['actors']
    budget = level_gate.danger_budget(1)
    assert len(actors) < BOOK_WARBAND, actors
    assert sum(level_gate.monster_cr(actor['monster']) for actor in actors) <= budget + 1e-9
    assert len(actors) <= 1, 'на 1-м уровне бюджет CR — четверть: один слабый враг'
    # Опасность включена числами движка, а не просьбой к рассказчику.
    assert state['nat20']['settings']['difficulty'] == 'solo_softer'
    # Мастер сказал, каким способом ведёт игру.
    assert 'опасность считается от твоего 1-го уровня' in result['level_gate']['decision_text']
    assert '× ' in result['level_gate']['decision_text'], result['level_gate']['decision_text']
    # Решение переживает перезапуск: новое подключение к той же базе.
    restarted = Nat20GameEngine(Store(tmp_path / 'level-gate.sqlite3'))
    again = restarted.level_gate(campaign)
    assert again['decided'] and again['decision'] == 'player_level'
    assert again['options'] == [] and again['ask'] == ''


def test_a_refused_book_hero_cannot_be_killed_on_the_first_round_by_the_numbers(tmp_path):
    """Проверка состава, а не везения кубика.

    Книга ставит восьмерых кобольдов на четырёх героев 1-го уровня. После
    «играем так» движок получает бой из одного кобольда (0.125 CR при бюджете
    0.25 CR на одиночку 1-го уровня) — это не «повезло на кубике», а состав.
    """
    api, campaign = book_campaign(tmp_path)
    api.accept_player_level(campaign, request_id='accept-2')
    current = api.store.campaign(campaign)
    actors = first_fight(api, campaign)['actors']
    hero_hp = current['state']['hero']['max_hp']
    # Один кобольд бьёт на 1d4+2: даже наибольший урон одного удара за раунд
    # не дотягивает до полного запаса хитов героя.
    strongest = max(level_gate.monster_cr(actor['monster']) for actor in actors)
    assert strongest <= level_gate.danger_budget(1) + 1e-9
    assert len(actors) * 6 <= hero_hp, (len(actors), hero_hp)


def test_rebuilding_the_hero_replaces_him_and_keeps_the_book(tmp_path):
    api, campaign = book_campaign(tmp_path)
    before = api.store.campaign(campaign)
    assert before['state']['hero']['level'] == 1
    rebuild = api.rebuild_hero(campaign, request_id='rebuild-1', build=hero_build(8),
                               name='Храбрец')
    current = api.store.campaign(campaign)
    state = current['state']
    assert state['hero']['name'] == 'Храбрец'
    assert state['hero']['level'] == 8, 'старый герой не остаётся в кампании'
    assert state['nat20']['definition']['build']['level'] == 8, 'лист считает движок'
    assert state['hero']['max_hp'] > 30, 'хиты 8-го уровня, а не 1-го'
    assert state['level_gate']['decision'] == 'rebuilt'
    assert rebuild['level_gate']['decided'] and rebuild['level_gate']['gap'] == ''
    # Книга та же: сцена и её бои не пересобраны.
    assert state['location'] == before['state']['location']
    assert len(first_fight(api, campaign)['actors']) == BOOK_WARBAND


def test_a_rebuild_below_the_book_is_refused_with_the_number(tmp_path):
    api, campaign = book_campaign(tmp_path)
    with pytest.raises(ValueError) as refusal:
        api.rebuild_hero(campaign, request_id='rebuild-low', build=hero_build(1))
    assert '8-й уровень' in str(refusal.value)
    assert api.store.campaign(campaign)['state']['hero']['level'] == 1


def test_after_the_rebuild_the_gate_is_closed_and_the_fight_opens(tmp_path):
    """После сборки Мастер начинает сцену: сверка закрыта, бой открывается."""
    api, campaign = book_campaign(tmp_path)
    api.rebuild_hero(campaign, request_id='rebuild-open', build=hero_build(8))
    state = api.store.campaign(campaign)['state']
    gate = level_gate.projection(state)
    assert gate['decided'] and gate['gap'] == '' and gate['options'] == [] and gate['ask'] == ''
    # Отказа нет: сверка решена, бой на уровне книги открывается.
    level_gate.guard(state, dict(kind='start_combat'))


def test_a_book_that_states_no_level_does_not_invent_a_requirement():
    state = dict(hero=dict(level=1), preparation={}, adventure={})
    assert level_gate.requirement(state) is None
    assert level_gate.projection(state) is None


def test_a_hero_stronger_than_the_book_is_a_mismatch_too():
    state = dict(hero=dict(level=12), preparation={},
                 adventure=dict(definition=dict(title='Книга для новичков',
                                                minimum_level=1, maximum_level=3)))
    gate = level_gate.projection(state)
    assert gate['gap'] == 'higher'
    assert '12-го' in gate['ask'] and '1-й уровень' in gate['ask']


def test_the_player_may_answer_with_words():
    assert level_gate.phrase('Играем так') == 'accept'
    assert level_gate.phrase('ладно, играем как есть') == 'accept'
    assert level_gate.phrase('пересобери героя') == 'rebuild'
    assert level_gate.phrase('Пересобрать героя на 8-й уровень') == 'rebuild'
    assert level_gate.phrase('иду в крепость') is None


def test_the_screen_carries_the_masters_question():
    """Экран получает вопрос Мастера в том же пакете, что и сцену."""
    from solo.nat20_screen import Nat20Screen
    from solo.play_screen import screen

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as folder:
        api, campaign = book_campaign(Path(folder))
        packet = screen(api.store, campaign, nat20_projector=Nat20Screen())
        gate = packet['snapshot']['level_gate']
        assert gate['required'] == 8 and gate['hero_level'] == 1
        assert gate['options'], 'экран получает оба пути, а не только текст'


# --- живой маршрут: то же слово, что набирает игрок --------------------------

def live_client(tmp_path, **kwargs):
    """Сервер на той же базе, что и кампания: маршруты экрана, не заглушки."""
    api, campaign = book_campaign(tmp_path, **kwargs)
    app = create_app(tmp_path / 'level-gate.sqlite3', MockLLM())
    return TestClient(app), api, campaign


def turn(client, campaign, text, version, request_id):
    return client.post('/api/campaigns/' + campaign + '/command-turn',
                       json=dict(request_id=request_id, text=text,
                                 expected_version=version))


def test_the_players_word_igraem_tak_is_a_campaign_fact_not_a_model_reply(tmp_path):
    client, api, campaign = live_client(tmp_path)
    before = api.store.campaign(campaign)
    answer = turn(client, campaign, 'играем так', before['version'], 'live-accept')
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body['status'] == 'resolved', body
    assert 'опасность считается от твоего 1-го уровня' in body['narrative'], body['narrative']
    current = api.store.campaign(campaign)
    assert current['state']['balance_override'] == 'player_level'
    # Ход не «поговорили и забыли»: состав первого боя уже другой.
    actors = first_fight(api, campaign)['actors']
    assert len(actors) <= 1, actors
    # Экран читает то же решение.
    gate = client.get('/api/campaigns/' + campaign + '/level-gate').json()['level_gate']
    assert gate['decided'] and gate['decision'] == 'player_level' and gate['options'] == []
    # Карточка кампании показывает, каким способом ведётся игра.
    card = [row for row in client.get('/api/chronicles').json()['campaigns']
            if row['id'] == campaign][0]
    assert card['balance_override'] == 'player_level'
    assert card['required_level'] == 8


def test_an_unrelated_word_keeps_the_masters_question_and_the_campaign_still(tmp_path):
    client, api, campaign = live_client(tmp_path)
    before = api.store.campaign(campaign)
    answer = turn(client, campaign, 'иду в крепость', before['version'], 'live-other')
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body['status'] == 'unsupported', body
    assert '8-й уровень' in body['narrative'] and 'твой герой 1-го' in body['narrative']
    after = api.store.campaign(campaign)
    assert after['version'] == before['version']
    assert not after['state'].get('balance_override')


def test_the_players_word_peresobrat_asks_for_the_editor_at_the_books_level(tmp_path):
    client, api, campaign = live_client(tmp_path)
    before = api.store.campaign(campaign)
    answer = turn(client, campaign, 'пересобрать героя', before['version'], 'live-rebuild')
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body['status'] == 'gate_rebuild', body
    assert '8-м уровне' in body['narrative'], body['narrative']
    # Без загруженного черновика книги редактор честно не открывается, но и
    # выдуманного контекста не появляется: rebuild=None, герой не тронут.
    assert body['rebuild'] is None
    after = api.store.campaign(campaign)
    assert after['version'] == before['version']
    assert after['state']['hero']['level'] == 1


def test_the_accept_route_records_the_same_decision(tmp_path):
    """Экран может нажать «Играем так» и кнопкой: тот же результат, что словом."""
    client, api, campaign = live_client(tmp_path)
    answer = client.post('/api/campaigns/' + campaign + '/level-gate/accept',
                         json=dict(request_id='route-accept'))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body['level_gate']['decision'] == 'player_level'
    state = api.store.campaign(campaign)['state']
    assert state['balance_override'] == 'player_level'


def test_the_rebuild_route_replaces_the_hero_without_starting_the_story(tmp_path):
    client, api, campaign = live_client(tmp_path)
    before = api.store.campaign(campaign)
    answer = client.post('/api/campaigns/' + campaign + '/level-gate/rebuild',
                         json=dict(request_id='route-rebuild', build=hero_build(8),
                                   prepared_spells=[], name='Храбрец'))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body['level_gate']['decided'] and body['level_gate']['gap'] == ''
    assert body['hero']['level'] == 8 and body['hero']['name'] == 'Храбрец'
    after = api.store.campaign(campaign)
    assert after['state']['hero']['level'] == 8
    # История не начата: ходов не прибавилось, сцена та же.
    assert after['state'].get('turn', 0) == before['state'].get('turn', 0)
    assert after['state']['location'] == before['state']['location']
