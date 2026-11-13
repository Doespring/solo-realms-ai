"""r197 — движок принимает добровольное «лечь» (SRD 5.2 Prone).

Замер r187: `PlayerIntent(intent_type='drop_prone')` падал `ValidationError`
(«Input should be 'attack', … or 'escape_grapple'») — литерала в `IntentType`
не было, поэтому правило `phb2024.phb01.drop_prone` («Падение на землю»,
`action_cost 0`, `movement_cost 0`) оставалось `blocked` с честной причиной
«в движке нет интента». r197 добавил литерал и обработчик; тесты держат ровно
измеренное: интент принимается, состояние `prone` приходит событием движка,
Действие/Бонусное действие/Реакция/движение не тратятся, ход не заканчивается,
повтор не плодит состояние, «Встать» работает после «Лечь», экран предлагает
кнопку, а строка журнала называет состояние.

«Верховая схватка» и «подводный бой» в этот файл не входят: там нет ни данных
в движке, ни интентов — это отдельная спецификация.
"""
from __future__ import annotations

import socket

import pytest

from dnd5e_engine import PlayerIntent

from solo.event_display import native_sentence
from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.nat20_screen import (Nat20Screen, adjacent_enemies, hero_conditions,
                               native_candidates)
from solo.storage import Store

from nat20_builds import legal

FOE = 'goblin-1'
HERO_SPEED = 30  # человек-воин 1 уровня: Скорость 30, «встать» — половина


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('SOLO_TEST', '1')
    connect = socket.socket.connect

    def local(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)
        raise AssertionError('External calls are forbidden in this test')

    monkeypatch.setattr(socket.socket, 'connect', local)


def encounter():
    return {'grid': dict(width=7, height=5, cell_size_ft=5, cover_cells={}),
            'hero_position': dict(x=1, y=1, z=0),
            'actors': [dict(id=FOE, monster='goblin-warrior', position=dict(x=2, y=1, z=0))]}


def campaign(tmp_path, name, seed=5):
    store = Store(tmp_path / (name + '.sqlite3'))
    api = Nat20GameEngine(store)
    build = legal(class_slug='fighter', species_slug='human', level=1,
                  equipment=['longsword', 'chain-mail', 'shield'])
    cid = api.create_experimental_campaign(name, build=build, encounters={'yard': encounter()},
                                           seed=seed, title='Двор', description='д')['id']
    command(store, api, cid, name + '-start', kind='start_combat', encounter_id='yard')
    return store, api, cid


def command(store, api, cid, request_id, **action):
    current = store.campaign(cid)
    return api.execute(cid, GameCommand(request_id=request_id, expected_version=current['version'],
                                        action=action))


def runtime(store, cid):
    return store.campaign(cid)['state']['nat20']


def hero(store, cid):
    view = runtime(store, cid)['combat']['view']
    return next(row for row in view['initiative'] if row['entity_id'] == 'hero')


def conditions(store, cid):
    return [row['condition'] for row in hero(store, cid)['conditions']]


def events(packet, kind):
    return [event for event in packet['native_events'] if event['type'] == kind]


def lie_down(store, api, cid, request_id='r197-prone'):
    return command(store, api, cid, request_id, kind='native_action', operation='drop_prone')


# --- 1. Интент существует ---------------------------------------------------

def test_the_intent_type_accepts_drop_prone():
    """r187: здесь был ValidationError — литерала в `IntentType` не было."""
    assert PlayerIntent(intent_type='drop_prone').intent_type == 'drop_prone'


# --- 2. Бесплатное «лечь», ход остаётся -------------------------------------

def test_dropping_prone_costs_nothing_and_keeps_the_turn(tmp_path):
    """SRD 5.2: «вы можете упасть ничком, не тратя действие» — ни одного бюджета."""
    store, api, cid = campaign(tmp_path, 'prone-free')
    before = dict(hero(store, cid))
    packet = lie_down(store, api, cid)
    after = hero(store, cid)
    assert 'prone' in conditions(store, cid), after['conditions']
    assert [event['condition'] for event in events(packet, 'condition_applied')] == ['prone']
    for budget in ('action_available', 'bonus_action_available', 'reaction_available',
                   'movement_remaining', 'attacks_remaining'):
        assert after[budget] == before[budget], (budget, before[budget], after[budget])
    # Ход не закончился: герой действует дальше в этом же ходу.
    swing = command(store, api, cid, 'r197-prone-swing', kind='native_action', operation='attack',
                    weapon_id='longsword', target_id=FOE)
    assert events(swing, 'attack_rolled'), swing['native_events']


# --- 3. Повтор не плодит состояние ------------------------------------------

def test_dropping_prone_twice_is_refused_with_a_reason(tmp_path):
    """Уже лежащему — отказ с причиной, а не молчаливое второе состояние.

    Симметрия с `stand_up` («нечего вставать»): повторное «лечь» отвергается
    ДО событий и до траты чего-либо, а хост называет причину по-человечески
    (`solo/game.py`: `already_prone`), а не «операция не дала эффекта».
    """
    store, api, cid = campaign(tmp_path, 'prone-twice')
    lie_down(store, api, cid, 'r197-prone-1')
    with pytest.raises(ValueError, match='already_prone'):
        lie_down(store, api, cid, 'r197-prone-2')
    assert conditions(store, cid).count('prone') == 1


def test_hero_conditions_are_names_not_active_condition_rows():
    """r197: записи ActiveCondition сравнивались со строкой — сравнение врало."""
    runtime = {'combat': {'view': {'initiative': [
        {'entity_id': 'hero', 'conditions': [{'condition': 'grappled', 'source_effect_id': 'e1'},
                                             {'condition': 'prone', 'source_effect_id': None}]}]}}}
    assert hero_conditions(runtime) == ['grappled', 'prone']
    assert hero_conditions({'combat': None}) == []


# --- 4. «Встать» после «Лечь» — половина скорости ---------------------------

def test_standing_up_after_dropping_prone_costs_half_speed(tmp_path):
    """Пара интентов замыкается: лечь бесплатно, встать — половина Скорости."""
    store, api, cid = campaign(tmp_path, 'prone-stand')
    lie_down(store, api, cid)
    assert hero(store, cid)['movement_remaining'] == HERO_SPEED
    packet = command(store, api, cid, 'r197-stand', kind='native_action', operation='stand_up')
    row = hero(store, cid)
    assert 'prone' not in conditions(store, cid), row['conditions']
    assert row['movement_remaining'] == HERO_SPEED - HERO_SPEED // 2, row['movement_remaining']
    assert [event['type'] for event in packet['native_events'] if event['type'] == 'condition_removed']


# --- 5. Экран предлагает кнопку, пока герой не лежит ------------------------

def test_the_screen_offers_lie_down_until_the_hero_is_prone(tmp_path):
    store, api, cid = campaign(tmp_path, 'prone-screen')
    state = runtime(store, cid)
    assert [enemy['id'] for enemy in adjacent_enemies(state)] == [FOE]
    offered = [action for _, action in native_candidates(state)
               if action.get('operation') == 'drop_prone']
    assert len(offered) == 1, offered
    panel = [row.get('parameters', {}).get('operation')
             for row in Nat20Screen().project(store.campaign(cid))['commands']]
    assert 'drop_prone' in panel, panel

    lie_down(store, api, cid)
    after = runtime(store, cid)
    assert not [action for _, action in native_candidates(after)
                if action.get('operation') == 'drop_prone'], 'лежащему предлагают лечь ещё раз'
    operations = [action.get('operation') for _, action in native_candidates(after)]
    assert 'stand_up' in operations, operations


# --- 6. Строка журнала называет состояние -----------------------------------

def test_the_sentence_names_the_condition_from_the_engine_event():
    """Нет события `condition_applied prone` — нет и утверждения «лёг»."""
    assert native_sentence({'operation': 'drop_prone'},
                           [{'type': 'condition_applied', 'target_id': 'hero',
                             'condition': 'prone'}]).startswith('Герой лёг')
    assert native_sentence({'operation': 'drop_prone'}, []) == 'Лечь не удалось: состояние не изменилось.'
