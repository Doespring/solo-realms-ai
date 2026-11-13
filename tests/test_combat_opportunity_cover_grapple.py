"""r187 — глава боя: возможности, укрытия, захват и толчок.

Аудит корпуса (`phb-01/04-combat`: blocked 10) и жалоба игрока «враг проходит
мимо без кары, укрытие ничего не даёт, захват не работает» проверены прогоном,
а не чтением. Замер до правки:

* провоцированная атака СЧИТАЕТСЯ движком (`attack_rolled` с
  `is_opportunity_attack=True` и урон), но журнал писал «Перемещение
  выполнено.» — игрок не знал, что его ударили;
* укрытие СЧИТАЕТСЯ (+2 / +5 к КЗ, полное — отказ атаки), но события не
  называли степень укрытия, а сценарии не размечают `cover_cells` — «укрытие
  ничего не даёт» было верно с экрана;
* захват и толчок СЧИТАЮТСЯ (`condition_applied grappled/prone`, скорость 0),
  но экран не предлагал ни одной кнопки: `native_candidates` их не отдавал.

Тесты держат ровно эти звенья: движок считает, хост называет числа и
предлагает действие.

Сид вынесен в аргумент: движок детерминирован, а порядок инициативы зависит от
сида (при 1/3/4 первым ходит гоблин). Числа ниже — из прогона, не из головы.
"""
from __future__ import annotations

import asyncio
import socket

import pytest

from solo.event_display import native_sentence
from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.nat20_content import cover_bonus, cover_degree
from solo.nat20_screen import Nat20Screen, adjacent_enemies, native_candidates
from solo.storage import Store

from nat20_builds import legal

FOE = 'goblin-1'
#: Сиды, на которых прогон дал нужное: 2 — спасбросок цели провален, 5 — пройден.
APPLIED_SEED, SAVED_SEED = 2, 5


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('SOLO_TEST', '1')
    connect = socket.socket.connect

    def local(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)
        raise AssertionError('External calls are forbidden in this test')

    monkeypatch.setattr(socket.socket, 'connect', local)


def encounter(foe=(2, 1), cover=None):
    return {'grid': dict(width=7, height=5, cell_size_ft=5, cover_cells=cover or {}),
            'hero_position': dict(x=1, y=1, z=0),
            'actors': [dict(id=FOE, monster='goblin-warrior',
                            position=dict(x=foe[0], y=foe[1], z=0))]}


def campaign(tmp_path, name, *, foe=(2, 1), cover=None, equipment=None, seed=5):
    store = Store(tmp_path / (name + '.sqlite3'))
    api = Nat20GameEngine(store)
    build = legal(class_slug='fighter', species_slug='human', level=1,
                  equipment=equipment or ['longsword', 'chain-mail', 'shield'])
    cid = api.create_experimental_campaign(name, build=build, encounters={'yard': encounter(foe, cover)},
                                           seed=seed, title='Двор', description='д')['id']
    command(store, api, cid, name + '-start', kind='start_combat', encounter_id='yard')
    return store, api, cid


def command(store, api, cid, request_id, **action):
    current = store.campaign(cid)
    return api.execute(cid, GameCommand(request_id=request_id, expected_version=current['version'],
                                        action=action))


def runtime(store, cid):
    return store.campaign(cid)['state']['nat20']


def combat_view(store, cid):
    return runtime(store, cid)['combat']['view']


def actor(store, cid, entity_id):
    return next(row for row in combat_view(store, cid)['initiative'] if row['entity_id'] == entity_id)


def conditions(store, cid, entity_id):
    return [row['condition'] for row in actor(store, cid, entity_id)['conditions']]


def events(packet, kind):
    return [event for event in packet['native_events'] if event['type'] == kind]


def outcome(packet):
    return packet['event'].payload['outcome']


# --- 1. Провоцированная атака ---------------------------------------------

def test_walking_out_of_reach_is_punished_and_the_player_reads_it(tmp_path):
    """SRD 5.2 Opportunity Attacks: уход из зоны досягаемости → Реакция врага."""
    store, api, cid = campaign(tmp_path, 'oa')
    before = actor(store, cid, 'hero')['hp_current']
    packet = command(store, api, cid, 'r187-oa', kind='move', position=dict(x=1, y=4, z=0))
    rolled = events(packet, 'attack_rolled')
    assert rolled and rolled[0]['is_opportunity_attack'] is True, packet['native_events']
    assert rolled[0]['attacker_id'] == FOE, rolled
    after = actor(store, cid, 'hero')['hp_current']
    assert after <= before, (before, after)
    text = outcome(packet)
    assert 'Перемещение выполнено.' in text, text
    assert 'Провоцированная атака' in text, text


def test_a_foe_out_of_reach_gets_no_reaction(tmp_path):
    """Шаг, не выходящий из досягаемости, Реакции не даёт."""
    store, api, cid = campaign(tmp_path, 'oa-safe', foe=(4, 1))
    packet = command(store, api, cid, 'r187-oa-safe', kind='move', position=dict(x=1, y=3, z=0))
    assert not events(packet, 'attack_rolled'), packet['native_events']
    assert 'Провоцированная' not in outcome(packet), outcome(packet)


# --- 2. Укрытие ------------------------------------------------------------

def engine_fight(degree, seed, *, hero_zone='0,0', foe_zone='3,0', equipment=('shortbow',)):
    """Движок напрямую: те же числа, что в бою, без подпроцесса хоста."""
    from dnd5e_engine import orchestrator as O
    from dnd5e_engine.build_party import build_party_member
    from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
    from dnd5e_engine.specs import EncounterMemberSpec, GridScene
    from dnd5e_srd_data.loader import BundledAssetLoader
    spec = CharacterBuildSpec(species_slug='human', class_slug='fighter', background_slug='soldier',
                              level=1, ability_scores=AbilityScores(strength=8, dexterity=16, constitution=10,
                                                                    intelligence=10, wisdom=8, charisma=8),
                              equipment=list(equipment))
    pc = build_party_member(spec, CombatInstance(entity_id='pc1', name='pc1', zone_id=hero_zone,
                                                 initiative=20, hp_max=40, hp_current=40),
                            loader=BundledAssetLoader())
    foe = EncounterMemberSpec(entity_id='m1', name='Foe', entity_type='Monster', initiative=1,
                              hp_current=60, hp_max=60, ac=12, zone_id=foe_zone, attack_bonus=4,
                              damage_dice='1d6', damage_type='slashing', base_speed=30,
                              behavior_profile='AGGRESSIVE')
    handle = asyncio.run(O.start_combat(session_id='r187', party=[pc], encounter=[foe],
                                        grid_scene=GridScene(width=8, height=6,
                                                             cover_cells={'1,0': degree} if degree else {}),
                                        rng_seed=seed)).handle
    asyncio.run(O.submit_player_intent(handle, 'pc1', O.PlayerIntent(intent_type='attack', target_id='m1',
                                                                     weapon_id='shortbow')))
    rolled = [event.model_dump(mode='json') for event in O.drain_pending_events(handle)
              if event.type == 'attack_rolled']
    return rolled[0] if rolled else None


def test_cover_changes_the_hit_number_by_two_and_five():
    """Тот же бросок, разная разметка: +2 и +5 — числа правила, а не догадка."""
    # Сид 3: бросок 13 (8+5) при КЗ 12 — попадание; половинное укрытие (КЗ 14)
    # и три четверти (КЗ 17) дают промах.
    none = engine_fight(None, 3)
    half = engine_fight('half', 3)
    three = engine_fight('three_quarters', 3)
    assert (none['natural'], none['roll_total'], half['natural'], half['roll_total']) == (8, 13, 8, 13)
    assert none['is_hit'] is True and half['is_hit'] is False and three['is_hit'] is False
    # Сид 7: бросок 16 — половинное ещё попадает, три четверти (КЗ 17) уже нет.
    half = engine_fight('half', 7)
    three = engine_fight('three_quarters', 7)
    assert half['roll_total'] == 16 and half['is_hit'] is True
    assert three['is_hit'] is False
    assert cover_bonus('half') == 2 and cover_bonus('three_quarters') == 5


def test_cover_degree_uses_the_engine_line_walk():
    """Степень считает движковый шов (`GridTopology.cover_between`), не хост."""
    grid = {'width': 7, 'height': 5, 'cell_size_ft': 5,
            'cover_cells': {'2,1': 'half', '3,1': 'three_quarters'}}
    assert cover_degree(grid, '1,1', '4,1') == 'three_quarters'
    assert cover_degree({'width': 7, 'height': 5, 'cover_cells': {'2,1': 'half'}}, '1,1', '4,1') == 'half'
    assert cover_degree({'width': 7, 'height': 5}, '1,1', '4,1') == 'none'
    # Клетка ЦЕЛИ считается (предмет в её пространстве укрывает), клетка
    # стрелка — нет: иначе стрелок укрывался бы от себя самого.
    assert cover_degree({'width': 7, 'height': 5, 'cover_cells': {'4,1': 'half'}}, '1,1', '4,1') == 'half'
    assert cover_degree({'width': 7, 'height': 5, 'cover_cells': {'1,1': 'half'}}, '1,1', '4,1') == 'none'


def test_total_cover_refuses_the_shot_instead_of_bonusing_it():
    """Полное укрытие — не бонус к КЗ, а невозможная атака (SRD 5.2)."""
    from dnd5e_engine import orchestrator as O
    from dnd5e_engine.build_party import build_party_member
    from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
    from dnd5e_engine.specs import EncounterMemberSpec, GridScene
    from dnd5e_srd_data.loader import BundledAssetLoader
    spec = CharacterBuildSpec(species_slug='human', class_slug='fighter', background_slug='soldier',
                              level=1, ability_scores=AbilityScores(strength=8, dexterity=16, constitution=10,
                                                                    intelligence=10, wisdom=8, charisma=8),
                              equipment=['shortbow'])
    pc = build_party_member(spec, CombatInstance(entity_id='pc1', name='pc1', zone_id='0,0',
                                                 initiative=20, hp_max=40, hp_current=40),
                            loader=BundledAssetLoader())
    foe = EncounterMemberSpec(entity_id='m1', name='Foe', entity_type='Monster', initiative=1,
                              hp_current=60, hp_max=60, ac=12, zone_id='3,0', attack_bonus=4,
                              damage_dice='1d6', damage_type='slashing', base_speed=30,
                              behavior_profile='AGGRESSIVE')
    handle = asyncio.run(O.start_combat(session_id='r187', party=[pc], encounter=[foe],
                                        grid_scene=GridScene(width=8, height=6,
                                                             cover_cells={'1,0': 'total'}),
                                        rng_seed=3)).handle
    asyncio.run(O.submit_player_intent(handle, 'pc1', O.PlayerIntent(intent_type='attack', target_id='m1',
                                                                     weapon_id='shortbow')))
    failed = [event.model_dump(mode='json') for event in O.drain_pending_events(handle)
              if event.type == 'attack_failed']
    assert failed and failed[0]['reason'] == 'out_of_range', failed


def test_the_hit_names_the_cover_and_the_numbers(tmp_path):
    """Укрытие, поднявшее КЗ, называется словами и числами, а не молчит."""
    store, api, cid = campaign(tmp_path, 'cover-name', foe=(4, 1),
                               cover={'2,1': 'half', '3,1': 'half'},
                               equipment=['shortbow', 'chain-mail'])
    packet = command(store, api, cid, 'r187-cover-name', kind='native_action', operation='attack',
                     weapon_id='shortbow', target_id=FOE)
    rolled = events(packet, 'attack_rolled')
    assert rolled, packet['native_events']
    ac = actor(store, cid, FOE)['ac']
    assert 'укрытие половинное (+2 к КЗ: %d → %d)' % (ac, ac + 2) in outcome(packet), outcome(packet)


# --- 3. Захват и толчок ----------------------------------------------------

def test_grapple_applies_grappled_and_zero_movement(tmp_path):
    """SRD 5.2 Grapple: провал спасброска → Схвачен, скорость 0, уйти нельзя."""
    store, api, cid = campaign(tmp_path, 'grapple', seed=APPLIED_SEED)
    packet = command(store, api, cid, 'r187-grapple', kind='native_action', operation='grapple',
                     target_id=FOE)
    foe = actor(store, cid, FOE)
    assert 'grappled' in conditions(store, cid, FOE), packet['native_events']
    assert foe['movement_remaining'] == 0, foe['movement_remaining']
    save = events(packet, 'save_rolled')[0]
    assert save['succeeded'] is False, save
    text = outcome(packet)
    assert text.startswith('Захват: цель схвачена'), text
    assert 'спасбросок %s против КС %s' % (save['roll_total'], save['dc']) in text, text


def test_a_passed_save_says_the_target_held(tmp_path):
    """Отказ движка — тоже результат, и он обязан читаться игроком."""
    store, api, cid = campaign(tmp_path, 'grapple-held', seed=SAVED_SEED)
    packet = command(store, api, cid, 'r187-grapple-held', kind='native_action', operation='grapple',
                     target_id=FOE)
    save = events(packet, 'save_rolled')[0]
    assert save['succeeded'] is True, save
    assert 'grappled' not in conditions(store, cid, FOE)
    assert outcome(packet).startswith('Захват не удался'), outcome(packet)


def test_shove_knocks_the_target_prone(tmp_path):
    """SRD 5.2 Unarmed Strike — Shove: провал спасброска → Сбит с ног."""
    store, api, cid = campaign(tmp_path, 'shove', seed=APPLIED_SEED)
    packet = command(store, api, cid, 'r187-shove', kind='native_action', operation='shove',
                     target_id=FOE, shove_push=False)
    assert 'prone' in conditions(store, cid, FOE), packet['native_events']
    assert outcome(packet).startswith('Толчок: цель сбита с ног'), outcome(packet)


def test_shove_that_fails_reads_as_a_failed_shove(tmp_path):
    store, api, cid = campaign(tmp_path, 'shove-held', seed=SAVED_SEED)
    packet = command(store, api, cid, 'r187-shove-held', kind='native_action', operation='shove',
                     target_id=FOE, shove_push=False)
    assert 'prone' not in conditions(store, cid, FOE)
    assert outcome(packet).startswith('Толчок не удался'), outcome(packet)


# --- 4. Два оружия ---------------------------------------------------------

def test_the_second_light_weapon_swing_adds_no_ability_modifier(tmp_path):
    """SRD 5.2 Two-Weapon Fighting: вторая атака лёгким оружием — 1d4 без Силы."""
    store, api, cid = campaign(tmp_path, 'twf', equipment=['shortsword', 'dagger', 'chain-mail'])
    main = command(store, api, cid, 'r187-twf-main', kind='native_action', operation='attack',
                   weapon_id='shortsword', target_id=FOE)
    assert events(main, 'attack_rolled'), main['native_events']
    assert actor(store, cid, 'hero')['light_weapon_swing_slug'] == 'shortsword'
    off = command(store, api, cid, 'r187-twf-off', kind='native_action', operation='attack',
                  weapon_id='dagger', target_id=FOE, use_bonus_action=True)
    assert events(off, 'attack_rolled'), off['native_events']
    assert actor(store, cid, 'hero')['offhand_attack_spent'] is True, 'окно второй атаки закрыто'
    damage = sum(event['amount'] for event in events(off, 'damage_applied'))
    # Кинжал 1d4; модификатор Силы (+3 у legal-сборки) в урон НЕ идёт.
    assert 0 <= damage <= 4, f'урон второй атаки {damage} — модификатор попал в урон'
    with pytest.raises(ValueError, match='no_action_economy'):
        command(store, api, cid, 'r187-twf-third', kind='native_action', operation='attack',
                weapon_id='dagger', target_id=FOE, use_bonus_action=True)


# --- 5. Экран предлагает действия -----------------------------------------

def test_the_screen_offers_grapple_and_shove_only_at_reach(tmp_path):
    store, api, cid = campaign(tmp_path, 'screen')
    state = runtime(store, cid)
    assert [enemy['id'] for enemy in adjacent_enemies(state)] == [FOE]
    offered = [action for _, action in native_candidates(state)
               if action.get('operation') in ('grapple', 'shove')]
    assert any(action['operation'] == 'grapple' and action.get('target_id') == FOE for action in offered)
    assert any(action['operation'] == 'shove' and action.get('shove_push') is False for action in offered)
    assert any(action['operation'] == 'shove' and action.get('shove_push') is True for action in offered)
    panel = [row.get('parameters', {}).get('operation') for row in Nat20Screen().project(store.campaign(cid))['commands']]
    assert 'grapple' in panel and 'shove' in panel, panel

    far_store, far_api, far_cid = campaign(tmp_path, 'screen-far', foe=(6, 4))
    far = runtime(far_store, far_cid)
    assert adjacent_enemies(far) == []
    assert not [action for _, action in native_candidates(far)
                if action.get('operation') in ('grapple', 'shove')]


def test_sentences_are_built_from_engine_events_not_guessed():
    """Строка собирается из событий движка: нет события — нет числа."""
    attack = native_sentence({'operation': 'attack'},
                             [{'type': 'attack_rolled', 'attacker_id': 'hero', 'target_id': 'm1',
                               'is_hit': False, 'is_crit': False, 'roll_total': 11}],
                             grid={'width': 7, 'height': 5, 'cover_cells': {'2,1': 'half'}},
                             view={'actor_zone': {'hero': '1,1', 'm1': '4,1'},
                                   'initiative': [{'entity_id': 'm1', 'ac': 15}]})
    assert attack == 'Атака: промах; укрытие половинное (+2 к КЗ: 15 → 17).', attack
    grapple = native_sentence({'operation': 'grapple'},
                              [{'type': 'save_rolled', 'target_id': 'm1', 'roll_total': 14,
                                'dc': 12, 'succeeded': True}])
    assert grapple == 'Захват не удался: цель устояла (спасбросок 14 против КС 12).', grapple
