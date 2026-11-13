"""r129: Помощь, Подготовка и Засада — движок исполняет, экран предлагает.

Проверяется исполнением, а не наличием строк:

* `help` без цели движок отвергает; с врагом в 5 футах — записывает грант
  `help` на ЦЕЛЬ-ВРАГА, и следующая атака союзника по этому врагу идёт с
  преимуществом (SRD 5.2 Help: «distract an enemy within 5 feet of you»);
* без второго члена отряда грант истекает на собственном TurnStarted
  помощника — поэтому экран кнопку «Помощь» не предлагает;
* `ready` с триггером `hit_by_attack` тратит Действие, не заканчивает ход и
  регистрирует `_PendingReaction`; подготовленная атака ОРУЖИЕМ снимается
  триггером без исполнения (пин разрешает только предустановленное
  ЗАКЛИНАНИЕ — `ready` + `shield` даёт `reaction_triggered`), поэтому r129b
  убрал кнопку с экрана и отказал операцию в чат-пути
  (`known_issue('actions','ready')`);
* `hide` требует укрытия три четверти/полного, тяжёлого затемнения или тьмы и
  НЕ тратит Действие — замеренный дефект пина, из-за которого хост отказывает
  операции (`nat20_prototype.known_issue`), а экран её не показывает.
"""
import asyncio
from copy import deepcopy
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'tests') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tests'))

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

from nat20_builds import legal
from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.nat20_screen import Nat20Screen, help_target, hide_rule_met, native_candidates
from solo.storage import Store

SCORES = AbilityScores(strength=14, dexterity=16, constitution=14,
                       intelligence=10, wisdom=12, charisma=10)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('SOLO_TEST', '1')
    connect = socket.socket.connect

    def local(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)
        raise AssertionError('External calls are forbidden in this test')

    monkeypatch.setattr(socket.socket, 'connect', local)


# --- движок напрямую -------------------------------------------------------


def member(entity_id, class_slug, zone, initiative):
    spec = CharacterBuildSpec(species_slug='human', class_slug=class_slug,
                              background_slug='criminal', level=1, ability_scores=SCORES)
    return build_party_member(
        spec,
        CombatInstance(entity_id=entity_id, name=entity_id, zone_id=zone,
                       initiative=initiative, hp_max=30, hp_current=30),
        loader=BundledAssetLoader(),
    )


def fight(party, foe_zone, cover=None, seed=5):
    return asyncio.run(O.start_combat(
        session_id='r129',
        party=party,
        encounter=[EncounterMemberSpec(
            entity_id='m1', name='Foe', entity_type='Monster', initiative=1,
            hp_current=50, hp_max=50, ac=12, zone_id=foe_zone,
            attack_bonus=4, damage_dice='1d4', damage_type='slashing',
            base_speed=30, behavior_profile='AGGRESSIVE')],
        grid_scene=GridScene(width=8, height=6, cover_cells=cover or {}),
        rng_seed=seed,
    )).handle


def submit(handle, actor, intent):
    asyncio.run(O.submit_player_intent(handle, actor, intent))


def hero_state(handle):
    live = O._get_live(handle)
    hero = next(c for c in live.initiative if c.entity_id == 'pc1')
    return dict(action=hero.action_available, reaction=hero.reaction_available,
                turn=live.initiative[live.current_turn_index].entity_id if not live.ended else None,
                pending=[(p.owner_id, p.trigger, p.spell_id) for p in live.pending_reactions],
                grants={k: list(v) for k, v in live.help_grants.items()},
                hidden=sorted(live.hidden_entities))


def test_help_without_a_target_is_refused_by_the_engine():
    handle = fight([member('pc1', 'fighter', '0,0', 20)], '1,1')
    with pytest.raises(O.IntentRejectedError, match='help target None'):
        submit(handle, 'pc1', O.PlayerIntent(intent_type='help'))
    assert hero_state(handle)['action'] is True, 'отказ не должен тратить Действие'


def test_help_against_an_adjacent_enemy_gives_the_ally_advantage():
    """SRD 5.2 Help: цель — враг в 5 футах, выгода достаётся атаке СОЮЗНИКА."""
    handle = fight([member('pc1', 'fighter', '0,0', 20), member('pc2', 'rogue', '1,0', 15)], '1,1')
    submit(handle, 'pc1', O.PlayerIntent(intent_type='help', target_id='m1'))
    state = hero_state(handle)
    assert state['grants'] == {'m1': ['pc1']}, state
    assert state['turn'] == 'pc2' and state['action'] is False
    submit(handle, 'pc2', O.PlayerIntent(intent_type='attack', target_id='m1', weapon_id='dagger'))
    rolled = [e for e in O.drain_pending_events(handle) if e.type == 'attack_rolled']
    assert rolled and rolled[0].advantage == 'advantage', rolled
    assert 'help' in (rolled[0].sources or []), rolled[0].sources
    assert O._get_live(handle).help_grants == {}, 'грант обязан быть израсходован'


def test_a_solo_help_grant_expires_before_the_helper_can_spend_it():
    """Без союзника Помощь не даёт ничего: грант снимается своим же TurnStarted."""
    handle = fight([member('pc1', 'fighter', '0,0', 20)], '1,1')
    submit(handle, 'pc1', O.PlayerIntent(intent_type='help', target_id='m1'))
    assert hero_state(handle)['grants'] == {'m1': ['pc1']}
    asyncio.run(O.advance_monster_turn(handle))
    state = hero_state(handle)
    assert state['grants'] == {}, 'грант обязан истечь на TurnStarted помощника'
    submit(handle, 'pc1', O.PlayerIntent(intent_type='attack', target_id='m1', weapon_id='longsword'))
    rolled = [e for e in O.drain_pending_events(handle) if e.type == 'attack_rolled']
    assert rolled and rolled[0].advantage == 'normal', rolled


def test_ready_arms_a_pending_reaction_without_ending_the_turn():
    handle = fight([member('pc1', 'fighter', '0,0', 20)], '1,1')
    submit(handle, 'pc1', O.PlayerIntent(intent_type='ready', reaction_trigger='hit_by_attack'))
    state = hero_state(handle)
    assert state['pending'] == [('pc1', 'hit_by_attack', None)], state
    assert state['action'] is False, 'Подготовка тратит Действие'
    assert state['turn'] == 'pc1', 'Подготовка не заканчивает ход'
    assert state['reaction'] is True, 'Реакция тратится только по триггеру'


def test_a_readied_weapon_attack_is_popped_without_resolving():
    """Пин разрешает только подготовленное заклинание: очередь пустеет молча."""
    handle = fight([member('pc1', 'fighter', '0,0', 20)], '1,1')
    submit(handle, 'pc1', O.PlayerIntent(intent_type='ready', reaction_trigger='hit_by_attack'))
    submit(handle, 'pc1', O.PlayerIntent(intent_type='pass'))
    O.drain_pending_events(handle)
    asyncio.run(O.advance_monster_turn(handle))
    events = O.drain_pending_events(handle)
    struck = [e for e in events if e.type in ('attack_rolled', 'damage_applied')]
    state = hero_state(handle)
    assert state['pending'] == [], 'триггер обязан снять подготовку с очереди'
    assert state['reaction'] is True, 'реакция не тратится: разрешать нечего'
    assert not [e for e in events if e.type == 'reaction_triggered'], events
    assert not [e for e in events if e.type == 'spell_cast'], events
    assert struck and struck[0].type == 'attack_rolled', 'монстр обязан был атаковать'


def test_a_readied_spell_reaction_does_fire_which_is_the_whole_ready_subset():
    """Граница пина: разрешается только подготовленное ЗАКЛИНАНИЕ (shield).

    Без этой границы блокер корпуса («подготовленная атака оружием снимается
    без разрешения») звучал бы как «Реакции в движке нет вообще» — а она есть,
    ровно для предустановленного заклинания.
    """
    handle = fight([member('pc1', 'wizard', '0,0', 20)], '1,1')
    submit(handle, 'pc1', O.PlayerIntent(intent_type='ready', reaction_trigger='hit_by_attack',
                                         spell_id='shield', slot_level=1))
    assert hero_state(handle)['pending'] == [('pc1', 'hit_by_attack', 'shield')]
    submit(handle, 'pc1', O.PlayerIntent(intent_type='pass'))
    O.drain_pending_events(handle)
    asyncio.run(O.advance_monster_turn(handle))
    events = O.drain_pending_events(handle)
    fired = [e for e in events if e.type == 'reaction_triggered']
    casts = [e for e in events if e.type == 'spell_cast']
    assert [e.reaction_name for e in fired] == ['shield'], events
    assert [e.spell_id for e in casts] == ['shield'], casts
    assert casts[0].slot_level == 1, 'подготовленное заклинание платит слот 1'
    assert hero_state(handle)['pending'] == []


def test_hide_requires_cover_and_the_pin_never_spends_the_action():
    handle = fight([member('pc1', 'rogue', '0,0', 20)], '5,0', cover=None)
    with pytest.raises(O.IntentRejectedError, match='Hide requires one of those'):
        submit(handle, 'pc1', O.PlayerIntent(intent_type='hide'))
    handle = fight([member('pc1', 'rogue', '0,0', 20)], '5,0', cover={'0,0': 'three_quarters'})
    submit(handle, 'pc1', O.PlayerIntent(intent_type='hide'))
    state = hero_state(handle)
    assert state['hidden'] == ['pc1'], 'успешная Засада даёт скрытое состояние'
    assert state['action'] is True, ('дефект пина: Засада не тратит Действие — '
                                     'поэтому хост отказывает операции')


# --- экран -----------------------------------------------------------------


def encounter(foe, cover=None):
    return {'grid': dict(width=7, height=5, cell_size_ft=5, cover_cells=cover or {}),
            'hero_position': dict(x=1, y=1, z=0),
            'actors': [dict(id='goblin-1', monster='goblin-warrior',
                            position=dict(x=foe[0], y=foe[1], z=0))]}


def campaign(tmp_path, name, foe, cover=None):
    store = Store(tmp_path / (name + '.sqlite3'))
    api = Nat20GameEngine(store)
    build = legal(class_slug='fighter', species_slug='human', level=1,
                  equipment=['longsword', 'chain-mail', 'shield'])
    cid = api.create_experimental_campaign(name, build=build,
                                           encounters={'yard': encounter(foe, cover)},
                                           seed=5, title='Двор', description='д')['id']
    current = store.campaign(cid)
    api.execute(cid, GameCommand(request_id=name + '-start', expected_version=current['version'],
                                 action=dict(kind='start_combat', encounter_id='yard')))
    return store, api, cid


def operations(packet):
    return [(c['parameters'].get('operation'), c['parameters'].get('target_id'))
            for c in packet['commands'] if c['parameters']['kind'] == 'native_action']


def test_screen_offers_neither_help_nor_ready_nor_hide(tmp_path):
    store, api, cid = campaign(tmp_path, 'plain', foe=(1, 2))
    current = store.campaign(cid)
    runtime = current['state']['nat20']
    assert help_target(runtime) is None, 'в одиночной кампании союзника нет'
    assert hide_rule_met(runtime) is False, 'герой стоит в чистом поле'
    panel = operations(Nat20Screen().project(current))
    # r187: враг в 5 футах (1,2) — экран обязан предложить Захват и Толчок
    # (SRD 5.2), они считаются движком. Остальную часть списка r129 не меняет.
    assert ('dodge', None) in panel and ('disengage', None) in panel, panel
    assert ('grapple', 'goblin-1') in panel and ('shove', 'goblin-1') in panel, panel
    assert [op for op in panel if op[0] in ('help', 'ready', 'hide')] == [], panel


def test_host_refuses_ready_because_a_readied_weapon_attack_never_resolves(tmp_path):
    """Чат-путём Подготовка тоже недоступна: иначе Действие тратится молча."""
    store, api, cid = campaign(tmp_path, 'ready', foe=(1, 2))
    current = store.campaign(cid)
    assert 'ready' in api.capabilities(current)['known_issues']
    with pytest.raises(ValueError, match='pre-armed SPELL'):
        api.execute(cid, GameCommand(request_id='r129b-ready', expected_version=current['version'],
                                     action=dict(kind='native_action', operation='ready',
                                                 reaction_trigger='hit_by_attack')))
    after = store.campaign(cid)
    assert after['version'] == current['version'], 'отказ не двигает состояние'
    hero = [a for a in after['state']['nat20']['combat']['view']['initiative']
            if a['entity_id'] == 'hero'][0]
    assert hero['action_available'] is True, 'отказ не тратит Действие'


def test_screen_hides_the_hide_button_the_host_refuses(tmp_path):
    """Условие движка выполнено, но хост отказывает — кнопки быть не должно."""
    store, api, cid = campaign(tmp_path, 'cover', foe=(1, 2), cover={'1,1': 'three_quarters'})
    current = store.campaign(cid)
    runtime = current['state']['nat20']
    assert hide_rule_met(runtime) is True, 'герой в укрытии три четверти'
    assert ('hide', None) in [(a.get('operation'), a.get('target_id'))
                              for _, a in native_candidates(runtime)]
    panel = operations(Nat20Screen().project(current))
    assert [op for op in panel if op[0] == 'hide'] == [], panel
    version = store.campaign(cid)['version']
    with pytest.raises(ValueError, match='does not spend'):
        api.execute(cid, GameCommand(request_id='r129-hide', expected_version=version,
                                     action=dict(kind='native_action', operation='hide')))


def test_help_candidate_names_the_enemy_when_an_ally_stands_beside_the_hero(tmp_path):
    """Второго героя хост не создаёт, поэтому проверяется чистый расчёт кандидата."""
    store, api, cid = campaign(tmp_path, 'ally', foe=(1, 2))
    runtime = deepcopy(store.campaign(cid)['state']['nat20'])
    assert help_target(runtime) is None
    view = runtime['combat']['view']
    view['party_ids'] = [*view['party_ids'], 'ally']
    view['actor_zone']['ally'] = '2,2'
    view['tracked_hp']['ally'] = 10
    view['initiative'].append({**view['initiative'][0], 'entity_id': 'ally',
                               'name': 'Союзник', 'hp_current': 10})
    target = help_target(runtime)
    assert target == {'id': 'goblin-1', 'name': 'Goblin Warrior'}, target
    panel = [(label, action) for label, action in native_candidates(runtime)
             if action.get('operation') == 'help']
    assert len(panel) == 1, panel
    label, action = panel[0]
    assert action['target_id'] == 'goblin-1' and 'Goblin Warrior' in label, panel

    # Тот же союзник, но враг далеко: кнопки нет.
    far = deepcopy(runtime)
    far['combat']['view']['actor_zone']['goblin-1'] = '6,4'
    assert help_target(far) is None
    assert not [a for _, a in native_candidates(far) if a.get('operation') == 'help']
