"""r197 — вторая атака лёгким оружием и бонусное действие.

Замер r187 (`reports/ENGINE_WORKSPACE_PROGRESS.md`): «вторая атака лёгким
оружием не тратит бонусное действие (`bonus_action_available` остаётся True),
хотя третья отбивается `no_action_economy`». r197 воспроизвёл это покадрово и
нашёл, что посылка шире факта:

* с НЕ-Nick лёгким оружием (топорик/дубинка) движок тратил Бонусное действие
  правильно и до правки;
* Бонусное действие оставалось только с кинжалом — а у кинжала печатное
  свойство Ник (SRD 5.2 «Ловкость»: доп. атака «как часть действия Атака, а
  не Бонусным действием»). То есть True — верно для Ника;
* НО `_consume_offhand_attack_budget` читал печатное свойство без проверки,
  что герой это мастерство ВЫБРАЛ, тогда как `activities/mastery.py` такое же
  печатное свойство у не выбравшего отвергает (`_earns_mastery`). У сборки без
  мастерств (`weapon_masteries = []`) кинжал давал Ника бесплатно.

Тесты держат обе стороны: выбранного Ника и его отсутствие. Числа — из прогона
(сид 5: первым ходит герой).
"""
from __future__ import annotations

import socket

import pytest

from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store

from nat20_builds import legal

FOE = 'goblin-1'


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


def campaign(tmp_path, name, equipment, picks=(), seed=5):
    """Сборка, у которой мастерства оружия либо не выбраны, либо выбраны явно.

    `legal()` одалживает только обязательные выборы (навыки, ASI): мастерство
    оружия — «советующий» выбор корпуса, поэтому пустой список остаётся
    пустым, а `picks` дописываются в тот же `selected_choices`.
    """
    from dnd5e_engine.build_spec import CharacterBuildSpec
    store = Store(tmp_path / (name + '.sqlite3'))
    api = Nat20GameEngine(store)
    build = legal(class_slug='fighter', species_slug='human', level=1, equipment=list(equipment))
    if picks:
        build['selected_choices'] = tuple(build['selected_choices']) + tuple(picks)
        build = CharacterBuildSpec(**build).model_dump(mode='json')
    cid = api.create_experimental_campaign(name, build=build, encounters={'yard': encounter()},
                                           seed=seed, title='Двор', description='д')['id']
    command(store, api, cid, name + '-start', kind='start_combat', encounter_id='yard')
    return store, api, cid


def command(store, api, cid, request_id, **action):
    current = store.campaign(cid)
    return api.execute(cid, GameCommand(request_id=request_id, expected_version=current['version'],
                                        action=action))


def hero(store, cid):
    view = store.campaign(cid)['state']['nat20']['combat']['view']
    return next(row for row in view['initiative'] if row['entity_id'] == 'hero')


def swing(store, api, cid, request_id, weapon):
    return command(store, api, cid, request_id, kind='native_action', operation='attack',
                   weapon_id=weapon, target_id=FOE)


def main_then_offhand(tmp_path, name, offhand, picks=()):
    """Основная атака коротким мечом (Vex), затем косая указанным оружием."""
    store, api, cid = campaign(tmp_path, name, ['shortsword', offhand, 'chain-mail'], picks=picks)
    main = swing(store, api, cid, name + '-main', 'shortsword')
    assert [event['type'] for event in main['native_events'] if event['type'] == 'attack_rolled'], \
        main['native_events']
    assert hero(store, cid)['light_weapon_swing_slug'] == 'shortsword'
    off = swing(store, api, cid, name + '-off', offhand)
    assert [event['type'] for event in off['native_events'] if event['type'] == 'attack_rolled'], \
        off['native_events']
    return store, api, cid


# --- 1. Ник без выбранного мастерства не работает ----------------------------

def test_a_printed_nick_the_hero_never_chose_still_spends_the_bonus_action(tmp_path):
    """Кинжал печатает Ник, но мастерство не выбрано — Бонусное действие тратится.

    До r197 здесь оставалось True: `_consume_offhand_attack_budget` читал
    `weapon.mastery == "nick"` и не спрашивал `_earns_mastery`, хотя
    `activities/mastery.py` для тех же печатных мастерств спрашивает.
    """
    store, api, cid = main_then_offhand(tmp_path, 'nick-unearned', 'dagger')
    row = hero(store, cid)
    assert row['weapon_masteries'] == [], row['weapon_masteries']
    assert row['offhand_attack_spent'] is True
    assert row['bonus_action_available'] is False, 'вторая атака не потратила Бонусное действие'


def test_a_bonus_action_feature_is_refused_once_the_offhand_swing_paid_for_itself(tmp_path):
    """Следствие траты: «Второе дыхание» после косой атаки отбивается экономикой."""
    store, api, cid = main_then_offhand(tmp_path, 'nick-unearned-wind', 'dagger')
    with pytest.raises(ValueError, match='no_action_economy'):
        command(store, api, cid, 'nick-unearned-wind', kind='native_action',
                operation='use_feature', feature_id='second-wind', target_id='hero')


# --- 2. Не-Nick лёгкое оружие и раньше тратило Бонусное действие -------------

def test_a_non_nick_light_weapon_spends_the_bonus_action(tmp_path):
    """Топорик (Vex, лёгкое) — вторая атака идёт Бонусным действием."""
    store, api, cid = main_then_offhand(tmp_path, 'vex-offhand', 'handaxe')
    assert hero(store, cid)['bonus_action_available'] is False


# --- 3. Выбранный Ник оставляет Бонусное действие ----------------------------

def test_the_chosen_nick_mastery_keeps_the_bonus_action(tmp_path):
    """SRD 5.2 Ник: доп. атака — часть действия Атака, Бонусное действие цело."""
    store, api, cid = main_then_offhand(tmp_path, 'nick-earned', 'dagger', picks=('dagger',))
    row = hero(store, cid)
    assert sorted(row['weapon_masteries']) == ['dagger'], row['weapon_masteries']
    assert row['offhand_attack_spent'] is True, 'окно доп. атаки закрыто'
    assert row['bonus_action_available'] is True, 'Ник съел Бонусное действие'
    # Бонусное действие не «висит пустым»: им правда можно воспользоваться.
    packet = command(store, api, cid, 'nick-earned-wind', kind='native_action',
                     operation='use_feature', feature_id='second-wind', target_id='hero')
    assert [event['type'] for event in packet['native_events'] if event['type'] == 'healing_applied'], \
        packet['native_events']


# --- 4. Третья атака отбивается при любом исходе -----------------------------

@pytest.mark.parametrize('offhand,picks', [('dagger', ()), ('dagger', ('dagger',)), ('handaxe', ())])
def test_a_third_light_attack_is_refused_by_action_economy(tmp_path, offhand, picks):
    name = 'third-%s-%s' % (offhand, len(picks))
    store, api, cid = main_then_offhand(tmp_path, name, offhand, picks=picks)
    with pytest.raises(ValueError, match='no_action_economy'):
        swing(store, api, cid, name + '-third', offhand)
