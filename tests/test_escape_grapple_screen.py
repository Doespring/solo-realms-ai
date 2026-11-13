"""r198/r199 — кнопка «Вырваться из захвата» на НАСТОЯЩЕМ состоянии боя.

r197 закрыл `hero_conditions`: кнопка перестала быть вечно ложной. r198 нашёл
настоящий дефект экрана (он называл случайного СОСЕДНЕГО врага и пропадал,
если враг не в 5 футах) и замерил, почему состояние «герой схвачен» в живой
игре недостижимо: у захвата монстров не было данных, а селектор его не
выбирал. r199 типизировал захват (`bugbear-stalker` Quick Grapple,
`constrictor-snake` Constrict), дал селектору ярус контроля и закрыл дыру
`grapple target_id=<сам герой>`, которой r198 СЕЯЛ состояние для этих тестов.

Поэтому фикстура файла теперь честная: `grappled` на героя ставит СУЩЕСТВО
своим ходом, а не герой сам себе. Проверяется то, за что отвечает экран:

1. Подпись кнопки называет ТОГО, КТО ДЕРЖИТ (имя из источника эффекта), и это
   не случайный сосед (`test_grappled_by_reads_...` держит обе формы origin).
2. Кнопка есть, пока стоит условие, даже если рядом никого нет и движение 0.
3. Бросок решает: успех снимает `grappled`, провал оставляет; КС — тот, что
   напечатан в стат-блоке, и он лежит в состоянии героя.
4. Провал назван в журнале честно.

Броски берутся из событий движка (`check_rolled`), а не пересчитываются.
"""
from __future__ import annotations

import copy
import os
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'tests') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tests'))

from nat20_builds import legal
from solo.event_display import native_sentence
from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.nat20_screen import (Nat20Screen, adjacent_enemies, grappled_by,
                               hero_conditions, native_candidates)
from solo.storage import Store

MONSTER = 'bugbear-stalker'
ESCAPE_DC = 13          # напечатан в SRD 5.2.1: «escape DC 13»
GRID = dict(x=3, y=2, z=0)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('SOLO_TEST', '1')
    connect = socket.socket.connect

    def local(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)
        raise AssertionError('External calls are forbidden in this test')

    monkeypatch.setattr(socket.socket, 'connect', local)


def campaign(tmp_path, name, *, monster=MONSTER, foe=(2, 1), seed=1):
    """Кампания с одним существом в 5 футах: оно умеет хватать (r199)."""
    store = Store(tmp_path / (name + '.sqlite3'))
    api = Nat20GameEngine(store)
    build = legal(class_slug='fighter', species_slug='human', level=3,
                  subclass_slug='champion',
                  equipment=['longsword', 'chain-mail', 'shield'])
    encounters = {'yard': {
        'grid': dict(width=7, height=5, cell_size_ft=5, cover_cells={}),
        'hero_position': dict(x=1, y=1, z=0),
        'actors': [dict(id='foe', monster=monster, position=dict(x=foe[0], y=foe[1], z=0))]}}
    cid = api.create_experimental_campaign(name, build=build, encounters=encounters,
                                           seed=seed, title='Двор', description='д')['id']
    command(store, api, cid, 'start', kind='start_combat', encounter_id='yard')
    return store, api, cid


def command(store, api, cid, request_id, **action):
    current = store.campaign(cid)
    return api.execute(cid, GameCommand(request_id=request_id,
                                        expected_version=current['version'], action=action))


def runtime(store, cid):
    return store.campaign(cid)['state']['nat20']


def view(store, cid):
    return runtime(store, cid)['combat']['view']


def row_of(store, cid, entity):
    return next(row for row in view(store, cid)['initiative'] if row['entity_id'] == entity)


def conditions(store, cid, entity='hero'):
    return [row['condition'] for row in row_of(store, cid, entity)['conditions']]


def hero_turn(store, api, cid, tag, limit=20):
    """Довести бой до хода героя: пока ходит монстр, экран действий не даёт."""
    for attempt in range(limit):
        live = view(store, cid)
        if live['ended'] or live['initiative'][live['current_turn_index']]['entity_id'] == 'hero':
            return not live['ended']
        command(store, api, cid, '%s-%d' % (tag, attempt), kind='end_turn')
    return False


def grappled_by_monster(store, api, cid, tag, limit=12):
    """Существо хватает героя своим ходом — путь, которым это идёт в игре."""
    for attempt in range(limit):
        if not hero_turn(store, api, cid, '%s-before-%d' % (tag, attempt)):
            return False
        if 'grappled' in conditions(store, cid):
            return True
        command(store, api, cid, '%s-%d' % (tag, attempt), kind='end_turn')
    return False


def escape_offers(store, cid):
    panel = Nat20Screen().project(store.campaign(cid))
    return [row for row in panel['commands']
            if (row.get('parameters') or {}).get('operation') == 'escape_grapple']


def escape_dc(store, cid):
    return next(row['save_dc'] for row in row_of(store, cid, 'hero')['conditions']
                if row['condition'] == 'grappled')


def press(store, api, cid, offer, tag):
    """Нажать предложенную кнопку так, как её нажал бы игрок."""
    parameters = offer['parameters']
    packet = command(store, api, cid, tag, **parameters)
    check = next(event for event in packet['native_events'] if event['type'] == 'check_rolled')
    return dict(roll=check['roll_total'], dc=check['dc'], succeeded=check['succeeded'],
                conditions=conditions(store, cid), events=packet['native_events'])


# --- 1. Подпись кнопки называет того, кто держит ----------------------------


def test_the_escape_button_names_the_creature_that_holds_the_hero(tmp_path):
    """r198 до правки: «Вырваться из захвата: Goblin Warrior» — держал не гоблин."""
    store, api, cid = campaign(tmp_path, 'r199-кнопка')
    assert grappled_by_monster(store, api, cid, 'hold'), row_of(store, cid, 'hero')['conditions']
    assert 'grappled' in conditions(store, cid)

    offers = escape_offers(store, cid)
    assert len(offers) == 1, offers
    assert offers[0]['label'] == 'Вырваться из захвата: Bugbear Stalker', offers[0]['label']
    assert offers[0]['parameters']['target_id'] == 'foe', offers[0]['parameters']


# --- 2. Схваченному герою кнопка нужна и без врага в 5 футах ----------------


def test_the_button_is_still_offered_when_the_grappler_is_out_of_reach(tmp_path):
    """Замер r198: `['grappled']`, движение 0, кнопки нет — тупик.

    Условие ставится в состоянии (как его ставит движок), а монстр отодвинут:
    экран обязан читать КС и источник из героя, а не искать соседа.
    """
    store, api, cid = campaign(tmp_path, 'r199-далеко')
    assert grappled_by_monster(store, api, cid, 'hold')
    state = copy.deepcopy(store.campaign(cid)['state'])
    for row in state['nat20']['combat']['view']['initiative']:
        if row['entity_id'] == 'foe':
            row['zone'] = '6,4'
    state['nat20']['combat']['view']['actor_zone']['foe'] = '6,4'
    runtime_fixture = state['nat20']
    assert adjacent_enemies(runtime_fixture) == [], runtime_fixture['combat']['view']['actor_zone']
    offers = [action for _, action in native_candidates(runtime_fixture)
              if action.get('operation') == 'escape_grapple']
    assert offers == [dict(kind='native_action', operation='escape_grapple',
                           target_id='foe')], offers


# --- 3. Бросок: успех снимает условие, провал оставляет ---------------------


def test_the_roll_decides_and_both_outcomes_happen_on_a_fixed_seed(tmp_path):
    store, api, cid = campaign(tmp_path, 'r199-бросок')
    assert grappled_by_monster(store, api, cid, 'hold')
    outcomes, seen = {}, []
    for cycle in range(6):
        if 'grappled' not in conditions(store, cid):
            if not grappled_by_monster(store, api, cid, 'again-%d' % cycle):
                break
        if not hero_turn(store, api, cid, 'cycle-%d' % cycle):
            break
        dc = escape_dc(store, cid)
        offers = escape_offers(store, cid)
        assert len(offers) == 1, offers
        result = press(store, api, cid, offers[0], 'press-%d' % cycle)
        seen.append(result)
        assert result['dc'] == dc == ESCAPE_DC, (result, dc)
        # SRD 5.2 «Ending a Grapple»: успех снимает состояние, провал — нет.
        assert ('grappled' in result['conditions']) is (not result['succeeded']), result
        outcomes[result['succeeded']] = outcomes.get(result['succeeded'], 0) + 1
        if len(outcomes) == 2:
            break
    assert set(outcomes) == {True, False}, seen


# --- 4. Провал броска честно назван в журнале -------------------------------


def test_a_failed_escape_keeps_the_condition_and_says_so(tmp_path):
    store, api, cid = campaign(tmp_path, 'r199-провал')
    assert grappled_by_monster(store, api, cid, 'hold')
    failure = None
    for cycle in range(6):
        if 'grappled' not in conditions(store, cid):
            if not grappled_by_monster(store, api, cid, 'again-%d' % cycle):
                break
        if not hero_turn(store, api, cid, 'f-%d' % cycle):
            break
        result = press(store, api, cid, escape_offers(store, cid)[0], 'f-%d' % cycle)
        if not result['succeeded']:
            failure = result
            break
    assert failure is not None, 'проба не дождалась провала броска'
    assert 'grappled' in failure['conditions'], failure
    assert native_sentence({'operation': 'escape_grapple'}, failure['events']).startswith(
        'Освобождение не удалось (проверка %d против КС %d)' % (failure['roll'], failure['dc']))


# --- 5. Источник захвата читается из эффекта движка -------------------------


def test_grappled_by_reads_the_engine_effect_origin():
    def fixture(origin, statuses=('grappled',)):
        return {'combat': {'view': {'initiative': [
            {'entity_id': 'hero', 'name': 'Герой', 'conditions': [
                {'condition': 'grappled', 'source_entity_id': 'implied:effect',
                 'source_effect_id': 'effect:grappled'}]},
            {'entity_id': 'foe', 'name': 'Bugbear Stalker', 'conditions': []}]},
            'active_effects': [{'id': 'effect:grappled', 'target_id': 'hero',
                                'origin': origin, 'statuses': list(statuses)}]}}

    # Безоружный захват героя (r198): `grapple:<приём>:<id>`.
    assert grappled_by(fixture('grapple:unarmed-strike:foe')) == dict(id='foe',
                                                                     name='Bugbear Stalker')
    # r199: типизированный эффект монстра — та же третья часть origin.
    assert grappled_by(fixture('cast:grappled:foe')) == dict(id='foe',
                                                            name='Bugbear Stalker')
    # Третья часть — не существо снимка: цель не выдумываем.
    assert grappled_by(fixture('cast:grappled:wild-shape')) is None
    # Источник неизвестен — цель не выдумываем, но кнопку не отбираем.
    unknown = {'combat': {'view': {'initiative': [
        {'entity_id': 'hero', 'name': 'Герой',
         'conditions': [{'condition': 'grappled', 'source_entity_id': 'implied:event',
                         'source_effect_id': None}]}]}, 'active_effects': []}}
    assert grappled_by(unknown) is None
    offered = [action for _, action in native_candidates(unknown)
               if action.get('operation') == 'escape_grapple']
    assert offered == [dict(kind='native_action', operation='escape_grapple')], offered
    assert hero_conditions(unknown) == ['grappled']
