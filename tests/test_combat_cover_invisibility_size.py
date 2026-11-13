"""r172: ranged fire in close combat, unseen targets and size are counted, not described.

Three book rules from `phb-01/04-combat` that used to be words only:

* `phb2024.phb01.ranged_close` — Disadvantage when a hostile creature is within
  5 feet of a ranged attacker (PHB 2024, page 27). The engine already rolled it
  but the player read only "Промах."; the reason and the formula now reach the text.
* `phb2024.php01.unseen_attack` — Disadvantage against a target you can't see,
  Advantage against a target that can't see you, and a miss when the target is
  not where the attack was aimed (PHB 2024, page 26).
* `phb2024.phb01.size` / `phb2024.phb01.move_through_creatures` — Large occupies
  2x2 cells, an ally's space is passable, a same-size hostile's space is not,
  and no creature may finish its movement inside another one (page 25).

The engine here is the host adapter (`solo/`), never `vendor/nat20`.
"""
import copy

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.check_text import WRONG_LOCATION, attack_text
from solo.combat import Combatant, CombatEngine, footprint
from solo.combat_service import CombatCommand, CombatService, StartEncounter, summarize
from solo.contracts import Hero
from solo.geometry import BattleMap, Cell, Position, rectangular_map
from solo.rulesets import BUILTINS
from solo.storage import Store
from test_rulesets import Dice


def battle(*, hero_weapon='shortbow', hero_position=(1, 1), foe_position=(2, 1),
           hero_size='Medium', foe_size='Medium', foe_team='enemy', foe_hp=20,
           ammo=5, draws=(15, 5), field=None):
    """A one-hero, one-creature skirmish on a plain grid."""
    hero = Combatant(id='hero', stats=Hero(name='Герой', weapon_proficiencies=['martial'],
                                           size=hero_size),
                     position=Position(x=hero_position[0], y=hero_position[1]),
                     team='hero', weapon=hero_weapon, ammo=ammo)
    foe = Combatant(id='foe', stats=Hero(name='Противник', hp=foe_hp, max_hp=foe_hp, ac=10,
                                         size=foe_size),
                    position=Position(x=foe_position[0], y=foe_position[1]),
                    team=foe_team, weapon='club')
    field = field or rectangular_map(12, 10)
    state = dict(hero=hero.stats.model_dump(), ruleset=BUILTINS['dnd2024'].model_dump(),
                 combat=dict(active=True, map=field.model_dump(),
                             actors=[hero.model_dump(), foe.model_dump()],
                             order=['hero', 'foe'], cursor=0, round=1,
                             lethal_risk_declared=True))
    return state, CombatEngine(state, rng=Dice(*draws))


# --- ranged_close ---------------------------------------------------------

def test_ranged_attack_next_to_a_hostile_is_disadvantage_named_in_the_roll_text():
    state, engine = battle(draws=(15, 5))
    result = engine.attack('hero', 'foe')
    assert result['roll']['mode'] == 'помеха'
    assert 'рядом враг' in result['roll']['disadvantage_reasons']
    line = attack_text(result)
    assert 'помеха: рядом враг' in line and 'd20 5' in line
    assert summarize(engine, [result]).startswith('Герой: атака по Противник.')


def test_a_hostile_that_cannot_see_the_shooter_still_imposes_disadvantage():
    """Source page 27 asks for an adjacent hostile, not for one that sees you."""
    state, engine = battle(draws=(15, 5))
    engine.actors['foe'].stats.conditions = ['blinded']
    result = engine.attack('hero', 'foe')
    # Blind target gives Advantage, the adjacent hostile gives Disadvantage,
    # and the two cancel into a plain roll.
    assert result['roll']['mode'] == 'обычный'
    # A blind target can neither defend nor see the shooter; the adjacent hostile
    # still adds Disadvantage, so the roll is plain and both sides are named.
    assert result['roll']['advantage_reasons'] and result['roll']['disadvantage_reasons']
    assert 'преимущество и помеха отменены' in attack_text(result)


# --- unseen_attack --------------------------------------------------------

def test_an_invisible_attacker_rolls_with_advantage():
    state, engine = battle(hero_weapon='longsword', draws=(15, 5, 5, 5))
    engine.actors['hero'].stats.conditions = ['invisible']
    result = engine.attack('hero', 'foe')
    assert result['roll']['mode'] == 'преимущество'
    assert result['roll']['advantage_reasons'] == ['нападающего не видно']
    assert 'преимущество: нападающего не видно' in attack_text(result)


def test_an_invisible_target_is_attacked_with_disadvantage_at_its_known_cell():
    state, engine = battle(hero_weapon='longsword', draws=(15, 5))
    engine.actors['foe'].stats.conditions = ['invisible']
    engine.data['last_seen'] = {'foe': dict(id='foe', name='Противник',
                                            position=dict(x=2, y=1, z=0), round=1)}
    result = engine.attack('hero', 'foe')
    assert result['roll']['mode'] == 'помеха'
    assert 'цель не видна' in result['roll']['disadvantage_reasons']
    assert not engine.actors['hero'].action          # the attack did happen
    # The attack gives the hidden target's real position away, hit or miss.
    assert engine.data['last_seen']['foe']['position'] == {'x': 2, 'y': 1, 'z': 0}


def test_a_target_that_left_the_aimed_cell_is_missed_without_a_roll():
    state, engine = battle(hero_position=(1, 1), foe_position=(6, 1), draws=())
    dice = Dice()
    engine.rng = dice
    engine.actors['foe'].stats.conditions = ['invisible']
    engine.data['last_seen'] = {'foe': dict(id='foe', name='Противник',
                                            position=dict(x=5, y=1, z=0), round=1)}
    result = engine.attack('hero', 'foe')
    assert result['wrong_location'] and result['roll'] is None and not result['hit']
    assert dice.calls == 0                            # no d20: the strike found nothing
    assert not engine.actors['hero'].action
    assert engine.actors['hero'].ammo == 4            # the arrow is spent either way
    assert attack_text(result) == WRONG_LOCATION


def test_a_target_never_seen_cannot_be_attacked_at_all():
    state, engine = battle(hero_weapon='longsword')
    engine.actors['foe'].stats.conditions = ['invisible']
    with pytest.raises(ValueError, match='не знаете, где находится цель'):
        engine.attack('hero', 'foe')
    assert engine.actors['hero'].action               # a refusal costs no action


# --- size ----------------------------------------------------------------

def test_a_large_creature_fills_four_cells_and_blocks_the_route():
    state, engine = battle(hero_weapon='longsword', foe_position=(5, 5), foe_size='Large')
    space = set(footprint(engine.actors['foe']))
    assert space == {(5, 5, 0), (6, 5, 0), (5, 6, 0), (6, 6, 0)}
    assert space <= engine.occupied()
    for cell in space:
        assert not engine.geometry.can_stand(cell, engine.occupied('hero'))
    with pytest.raises(ValueError, match='пространстве другого существа'):
        engine.movement_path('hero', (6, 5, 0))


def test_melee_reach_to_a_large_creature_is_measured_to_its_near_edge():
    state, engine = battle(hero_weapon='longsword', hero_position=(4, 5),
                           foe_position=(5, 5), foe_size='Large', draws=(15, 5))
    result = engine.attack('hero', 'foe')
    assert result['distance_ft'] == 5 and result['target_ac'] == 10


def test_encounter_start_accepts_large_and_rejects_overlapping_spaces():
    hero = Combatant(id='hero', stats=Hero(name='Герой', weapon_proficiencies=['martial']),
                     position=Position(x=1, y=1), team='hero', weapon='longsword')
    large = Combatant(id='foe', stats=Hero(name='Великан', hp=20, max_hp=20, ac=10, size='Large'),
                      position=Position(x=5, y=5), team='enemy', weapon='club')
    state = dict(hero=hero.stats.model_dump(), ruleset=BUILTINS['dnd2024'].model_dump())
    engine = CombatEngine.start(state, rectangular_map(12, 10), [hero, large], rng=Dice(20, 1))
    assert len(set(footprint(engine.actors['foe']))) == 4
    neighbour = large.model_copy(update=dict(id='foe-2', position=Position(x=6, y=6)))
    with pytest.raises(ValueError, match='пересекается'):
        CombatEngine.start(dict(hero=hero.stats.model_dump(), ruleset=BUILTINS['dnd2024'].model_dump()),
                           rectangular_map(12, 10), [hero, large, neighbour], rng=Dice(20, 1))


# --- move_through_creatures ----------------------------------------------

def corridor(occupant_size, occupant_team):
    """A one-cell-wide corridor: only row y=1 is walkable."""
    cells = []
    for y in range(3):
        for x in range(5):
            cells.append(Cell(x=x, y=y, blocked=y != 1))
    field = BattleMap(width=5, height=3, cells=cells)
    state, engine = battle(hero_position=(1, 1), foe_position=(2, 1), field=field,
                           foe_size=occupant_size, foe_team=occupant_team,
                           hero_weapon='longsword', draws=(1, 1, 1, 1, 1, 1))
    return state, engine


def test_a_same_size_hostile_space_blocks_the_only_route():
    state, engine = corridor('Medium', 'enemy')
    with pytest.raises(ValueError, match='Нет допустимого пути'):
        engine.movement_path('hero', (3, 1, 0))


def test_an_allies_space_is_passable_at_normal_cost():
    state, engine = corridor('Medium', 'ally')
    path, cost = engine.movement_path('hero', (3, 1, 0))
    assert path == [(1, 1, 0), (2, 1, 0), (3, 1, 0)] and cost == 10
    assert engine.move('hero', (3, 1, 0))[-1]['kind'] == 'move_step'


def test_a_creature_two_sizes_away_is_passable_but_difficult():
    state, engine = corridor('Large', 'enemy')
    engine.actors['hero'].stats.size = 'Small'
    # The Large creature fills (2,1)-(3,2); both of its corridor cells cost double.
    path, cost = engine.movement_path('hero', (4, 1, 0))
    assert path == [(1, 1, 0), (2, 1, 0), (3, 1, 0), (4, 1, 0)] and cost == 25
    with pytest.raises(ValueError, match='завершить перемещение'):
        engine.movement_path('hero', (3, 1, 0))


def test_movement_cannot_finish_inside_another_creature():
    state, engine = corridor('Medium', 'ally')
    with pytest.raises(ValueError, match='завершить перемещение'):
        engine.movement_path('hero', (2, 1, 0))


# --- combat_end ----------------------------------------------------------

def test_the_engine_ends_a_battle_only_when_a_side_is_out():
    state, engine = battle(hero_weapon='longsword', foe_hp=8, draws=(20, 8, 8))
    engine.attack('hero', 'foe')
    assert engine.actors['foe'].stats.hp == 0
    assert engine.data['active']                     # the turn is not over yet
    engine.end_turn('hero')
    assert not engine.data['active'] and engine.data['ended'] == 'victory'


def test_hero_death_ends_the_battle_as_defeat():
    state, engine = battle()
    engine.data['lethal_risk_declared'] = True
    engine.damage(engine.actors['hero'], 30, 'fire')
    assert engine.actors['hero'].dead
    engine.end_turn('hero')
    assert not engine.data['active'] and engine.data['ended'] == 'defeat'


# --- through the HTTP route ---------------------------------------------

def test_combat_route_delivers_the_formula_and_refuses_an_occupied_cell(tmp_path):
    pytest.skip('r210: HTTP-путь прежнего боевого калькулятора закрыт — кампания без nat20 не открывается (solo/playable_ui.py:186, solo/app.py:314), а для нативной POST /combat/* отвечает 409 command_backend_required (проба r225). Нужен перевод проверки на экран Nat20.')

    store = Store(tmp_path / 'r172.sqlite3')
    cid = store.create('Бой', Hero(name='Герой', weapon_proficiencies=['martial']))['id']
    service = CombatService(store, rng=Dice(15, 5, 5, 5, 5, 5, 5, 5))
    field = rectangular_map(12, 10)
    service.start(cid, StartEncounter(request_id='start', confirm=True, hero_weapon='shortbow',
                                      imported_map=field, hero_position=Position(x=1, y=1),
                                      enemy_position=Position(x=2, y=1)))
    client = TestClient(create_app(store.path))
    shot = client.post('/api/campaigns/' + cid + '/combat/command',
                       json=dict(request_id='shot', kind='attack', target='foe-1'))
    assert shot.status_code == 200, shot.text
    text = shot.json()['results'][0]['text']
    assert 'помеха: рядом враг' in text and 'd20' in text
    blocked = client.post('/api/campaigns/' + cid + '/combat/command',
                          json=dict(request_id='step', kind='move', position=dict(x=2, y=1, z=0)))
    assert blocked.status_code == 409
    assert 'завершить перемещение' in blocked.json()['detail']


def test_new_attack_fields_do_not_leak_a_hidden_attackers_weapon():
    from solo.combat_service import safe_operations
    state, engine = battle()
    engine.actors['foe'].stats.conditions = ['invisible']
    op = dict(kind='attack', actor='foe', target='hero', weapon='club', weapon_name='Дубинка',
              target_ac=14, distance_ft=5, cover_bonus=0, hit=False, damage_dice=[3],
              advantage_reasons=['нападающего не видно'], disadvantage_reasons=[],
              roll={'selected': 3, 'bonus': 2, 'total': 5, 'mode': 'преимущество'})
    public = safe_operations(engine, [op])[0]
    assert not set(public) & {'actor', 'weapon', 'weapon_name', 'target_ac', 'roll',
                              'damage_dice', 'distance_ft', 'cover_bonus',
                              'advantage_reasons', 'disadvantage_reasons'}
    assert public['hit'] is False
