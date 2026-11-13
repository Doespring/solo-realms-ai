"""r173: вдохновение, переброс одной к20, КЗ и характеристика атаки считаются.

Пять правил `phb-01/01-core`, которые были словами:

* ``heroic_inspiration`` — ресурс листа (``Hero.resources``), трата на переброс,
  излишек передаётся другому герою группы и не копится (PHB 2024, стр. 13);
* ``single_die_replacement`` — заменяется ровно одна выбранная к20, остальные
  кости остаются теми же и генератор не вызывается (стр. 13);
* ``base_ac`` — один расчёт КЗ на лист и бой (``solo/armor_class.py``), второй
  базовый расчёт не складывается (стр. 12);
* ``attack_ability`` — характеристика атаки берётся из правила: рукопашное —
  Сила, дальнобойное — Ловкость, фехтовальное — выбор (стр. 12);
* ``voluntary_save_failure`` — добровольный провал не бросает кость (стр. 11).

Движок здесь хостовый (``solo/``); ``vendor/nat20`` не трогается.
"""
import pytest

from solo.armor_class import armor_class
from solo.check_text import attack_text
from solo.combat import Combatant, CombatEngine
from solo.combat_service import summarize
from solo.contracts import Hero, Resource
from solo.geometry import Position, rectangular_map
from solo.inventory import CATALOG, InventoryEngine, new_item
from solo.rulesets import BUILTINS, RulesetEngine, inspiration_charges, replace_die
from test_rulesets import Dice


class ForbiddenDice:
    """Генератор, который падает при любой попытке бросить кость."""

    calls = 0

    def randint(self, low, high):
        ForbiddenDice.calls += 1
        raise AssertionError('кость не должна бросаться')


def hero_with_inspiration(*, species='', features=(), dexterity=14):
    hero = Hero(name='Герой', species=species, features=list(features),
                abilities=dict(strength=14, dexterity=dexterity, constitution=14,
                               intelligence=10, wisdom=12, charisma=10))
    hero.resources['heroic_inspiration'] = Resource(current=1, maximum=1, recovery='none')
    return hero


def battle(*draws, hero=None, weapon='longsword', ally=None, enemy_position=(2, 1)):
    hero = hero or Hero(name='Герой', weapon_proficiencies=['martial'])
    hero_actor = Combatant(id='hero', stats=hero, position=Position(x=1, y=1),
                           team='hero', weapon=weapon, ammo=5)
    foe = Combatant(id='foe', stats=Hero(name='Противник', hp=100, max_hp=100, ac=10),
                    position=Position(x=enemy_position[0], y=enemy_position[1]),
                    team='enemy', weapon='club')
    actors = [hero_actor, foe]
    if ally is not None:
        actors.append(Combatant(id='ally', stats=ally, position=Position(x=1, y=2),
                                team='ally', weapon='club'))
    state = dict(hero=hero_actor.stats.model_dump(), ruleset=BUILTINS['dnd2024'].model_dump(),
                 combat=dict(active=True, map=rectangular_map(12, 10).model_dump(),
                             actors=[actor.model_dump() for actor in actors],
                             order=[actor.id for actor in actors], cursor=0, round=1,
                             lethal_risk_declared=True, explored=[], last_seen={},
                             pending_reaction=None, pending_death=None))
    return state, CombatEngine(state, rng=Dice(*draws))


# --- heroic_inspiration ---------------------------------------------------

def test_spending_inspiration_rerolls_one_die_and_decreases_the_resource():
    state, engine = battle(13, 2, hero=hero_with_inspiration())
    result = engine.attack('hero', 'foe', inspiration=True)
    roll = result['roll']
    assert roll['dice'] == [2] and roll['selected'] == 2
    assert roll['inspiration'] == dict(spent=1, remaining=0, die_index=0, before=13, after=2,
                                       total_before=15, total_after=4)
    assert inspiration_charges(engine.actors['hero'].stats) == 0
    engine.flush()
    assert state['hero']['resources']['heroic_inspiration']['current'] == 0
    line = attack_text(result)
    assert 'до переброса d20 13' in line and 'вдохновение: переброшено 13 → 2' in line


def test_reroll_without_inspiration_is_refused_instead_of_silently_rolling():
    state, engine = battle(13, 2)
    with pytest.raises(ValueError, match='Героического вдохновения нет'):
        engine.attack('hero', 'foe', inspiration=True)
    assert engine.actors['hero'].action  # отказ не съел действие


def test_inspiration_overflow_is_gifted_and_never_stacks_in_the_sheet():
    ally = Hero(name='Спутник')
    state, engine = battle(hero=hero_with_inspiration(), ally=ally)
    first = engine.gain_inspiration('hero')
    assert first['gained'] == 0 and first['remaining'] == 1
    assert 'не накапливается' in first['text']
    assert inspiration_charges(engine.actors['hero'].stats) == 1
    gift = engine.gain_inspiration('hero', gift_to='ally')
    assert gift['kind'] == 'inspiration_gift' and gift['target'] == 'ally'
    assert gift['remaining'] == 0 and gift['target_remaining'] == 1
    engine.flush()
    assert inspiration_charges(CombatEngine(state).actors['ally'].stats) == 1
    with pytest.raises(ValueError, match='передаётся герою группы'):
        CombatEngine(state).give_inspiration('hero', 'foe')
    later = CombatEngine(state)
    later.gain_inspiration('hero')
    later.flush()
    with pytest.raises(ValueError, match='уже есть вдохновение'):
        CombatEngine(state).give_inspiration('hero', 'ally')


def test_human_gets_inspiration_at_the_start_of_the_day_and_it_is_visible_in_state():
    rules = RulesetEngine(BUILTINS['dnd2024'])
    state = dict(hero=Hero(name='Человек', species='human').model_dump())
    result = rules.rest(state, 'long')
    assert result['inspiration'] == dict(granted=1, remaining=1)
    assert state['hero']['resources']['heroic_inspiration']['current'] == 1
    other = dict(hero=Hero(name='Эльф', species='elf').model_dump())
    assert 'inspiration' not in rules.rest(other, 'long')


# --- single_die_replacement ----------------------------------------------

def test_one_named_d20_is_replaced_and_the_other_dice_are_untouched():
    rolled = dict(dice=[3, 18], selected=18, bonus=5, total=23, mode='преимущество')
    replaced = replace_die(rolled, 0, 5)
    assert replaced['dice'] == [5, 18] and replaced['selected'] == 18
    assert replaced['replaced'] == dict(index=0, before=3, after=5, sides=20)
    assert rolled == dict(dice=[3, 18], selected=18, bonus=5, total=23, mode='преимущество')
    with pytest.raises(ValueError, match='Индекс кости'):
        replace_die(rolled, 2, 7)
    with pytest.raises(ValueError, match='Замена вне диапазона'):
        replace_die(rolled, 0, 21)


def test_replacement_does_not_roll_a_die_and_switches_the_selected_one():
    rolled = dict(dice=[19, 7], selected=7, bonus=0, total=7, mode='помеха')
    ForbiddenDice.calls = 0
    replaced = replace_die(rolled, 1, 2)
    assert ForbiddenDice.calls == 0
    assert replaced['dice'] == [19, 2] and replaced['selected'] == 2


# --- base_ac --------------------------------------------------------------

def equipped_state(*, features=(), dexterity=14):
    hero = Hero(name='Герой', features=list(features), ac=12,
                abilities=dict(strength=14, dexterity=dexterity, constitution=14,
                               intelligence=10, wisdom=12, charisma=10),
                armor_proficiencies=['light', 'medium', 'heavy', 'shields'])
    state = dict(hero=hero.model_dump(), ruleset=BUILTINS['dnd2024'].model_dump(),
                 economy=dict(items=[], wallet_cp=0, equipment={}, base_ac=None))
    return state


def equip(state, catalog, slot):
    inventory = InventoryEngine(state)
    inventory.data.items.append(new_item(catalog, 1, 'hero', 'тест r173'))
    inventory.equip(next(item.id for item in inventory.data.items if item.catalog == catalog), slot)
    inventory.flush()
    return state


def test_sheet_row_and_battle_read_the_same_single_ac_calculation():
    state = equip(equip(equipped_state(), 'chain_mail', 'armor'), 'shield', 'shield')
    hero = Hero.model_validate(state['hero'])
    inventory = InventoryEngine(state)
    armor, shield = inventory.armor_parameters()
    calculated = armor_class(hero, armor=armor, shield=shield)
    sheet = RulesetEngine(BUILTINS['dnd2024']).sheet(hero)['hero']
    assert calculated['ac'] == 18 == state['hero']['ac'] == sheet['ac']
    assert calculated['calculation'] == 'armor' and calculated['calculations_used'] == 1
    engine = CombatEngine(dict(state, combat=dict(active=True, map=rectangular_map(12, 10).model_dump(),
        actors=[Combatant(id='hero', stats=hero, position=Position(x=1, y=1), team='hero').model_dump(),
                Combatant(id='foe', stats=Hero(name='Противник', hp=5, max_hp=5, ac=10),
                          position=Position(x=2, y=1), team='enemy', weapon='club').model_dump()],
        order=['hero', 'foe'], cursor=0, round=1, lethal_risk_declared=True)))
    assert engine.actors['hero'].stats.ac == sheet['ac'] == 18


def test_second_base_calculation_is_named_and_not_stacked():
    state = equipped_state(features=['unarmored_barbarian'])
    inventory = InventoryEngine(state)
    inventory.data.items.append(new_item('chain_mail', 1, 'hero', 'тест r173'))
    inventory.equip(next(item.id for item in inventory.data.items if item.catalog == 'chain_mail'), 'armor')
    result = inventory.recalculate_ac()
    assert result['ac'] == 16 and result['calculations_used'] == 1
    assert [item['id'] for item in result['ignored']] == ['unarmored_barbarian']


def test_dexterity_change_reaches_ac_because_the_formula_is_recomputed():
    state = equip(equipped_state(dexterity=14), 'leather', 'armor')
    assert state['hero']['ac'] == 13  # 11 + ловкость +2
    state['hero']['abilities']['dexterity'] = 18
    InventoryEngine(state).recalculate_ac()
    assert state['hero']['ac'] == 15
    assert InventoryEngine(state).data.base_ac == 12  # подтверждённая база не перезаписана


# --- attack_ability -------------------------------------------------------

def test_melee_weapon_refuses_a_dexterity_attack_from_the_rule():
    state, engine = battle(15)
    with pytest.raises(ValueError, match='не разрешена'):
        engine.attack('hero', 'foe', ability='dexterity')


def test_finesse_weapon_chooses_the_better_modifier_and_says_why():
    state, engine = battle(15, 4, hero=Hero(name='Герой', abilities=dict(
        strength=12, dexterity=18, constitution=10, intelligence=10, wisdom=10, charisma=10)),
        weapon='dagger')
    result = engine.attack('hero', 'foe')
    assert result['ability'] == 'dexterity'
    assert 'лучший модификатор' in result['ability_reason']
    assert 'характеристика: ' in attack_text(result)


def test_finesse_weapon_honours_an_explicit_strength_choice_and_damage_uses_it():
    hero = Hero(name='Герой', abilities=dict(strength=16, dexterity=18, constitution=10,
                                             intelligence=10, wisdom=10, charisma=10))
    state, engine = battle(15, 3, hero=hero, weapon='dagger')
    result = engine.attack('hero', 'foe', ability='strength')
    assert result['ability'] == 'strength' and result['ability_reason'] == 'выбор игрока из правила'
    assert result['hp_damage'] == 3 + 3  # 1d4 + модификатор Силы (+3), не Ловкости (+4)


def test_ranged_weapon_is_dexterity_by_the_rule_even_when_strength_is_higher():
    hero = Hero(name='Герой', abilities=dict(strength=18, dexterity=12, constitution=10,
                                             intelligence=10, wisdom=10, charisma=10),
                weapon_proficiencies=['martial'])
    state, engine = battle(15, 4, hero=hero, weapon='shortbow')
    result = engine.attack('hero', 'foe')
    assert result['ability'] == 'dexterity' and 'Ловкость' in result['ability_reason']


# --- voluntary_save_failure ----------------------------------------------

def test_voluntary_failure_draws_no_die_at_all():
    rules = RulesetEngine(BUILTINS['dnd2024'])
    ForbiddenDice.calls = 0
    result = rules.saving_throw(Hero(name='Герой'), 'dexterity', 15, voluntary=True, rng=ForbiddenDice())
    assert ForbiddenDice.calls == 0
    assert result['draws'] == 0 and result['dice'] == [] and result['selected'] is None
    assert result['success'] is False and result['voluntary'] is True
    assert 'кость не бросалась' in result['formula']


def test_ordinary_save_still_rolls_a_die():
    rules = RulesetEngine(BUILTINS['dnd2024'])
    result = rules.saving_throw(Hero(name='Герой'), 'dexterity', 15, rng=Dice(13))
    assert result['dice'] == [13] and result['success'] is True
    assert 'voluntary' not in result


# --- ревью r173: найденные дефекты ---------------------------------------

def test_hidden_attacker_inspiration_and_ability_stay_private():
    """Скрытый враг не отдаёт игроку ни характеристику, ни значения переброса."""
    from solo.combat_service import safe_operations
    foe = Hero(name='Скрытый', conditions=['invisible'], weapon_proficiencies=['simple'],
               abilities=dict(strength=10, dexterity=18, constitution=10,
                              intelligence=10, wisdom=10, charisma=10))
    foe.resources['heroic_inspiration'] = Resource(current=1, maximum=1, recovery='none')
    state, engine = battle(15, 4, 6, hero=Hero(name='Герой'), weapon='club')
    engine.actors['foe'].stats = foe
    engine.actors['foe'].last_seen_hero = engine.actors['hero'].position.model_copy()
    engine.data['order'] = ['foe', 'hero']
    engine.data['cursor'] = 0
    operation = engine.attack('foe', 'hero', inspiration=True)
    engine.flush()
    assert not CombatEngine(state).sees(CombatEngine(state).actors['hero'],
                                        CombatEngine(state).actors['foe'])
    public = safe_operations(engine, [operation])[0]
    assert 'ability' not in public and 'ability_reason' not in public
    assert 'inspiration' not in public
    assert 'roll' not in public and 'weapon_name' not in public


def test_refused_reroll_does_not_touch_the_generator():
    state, engine = battle(hero=Hero(name='Герой'))
    engine.rng = ForbiddenDice()
    with pytest.raises(ValueError, match='Героического вдохновения нет'):
        engine.attack('hero', 'foe', inspiration=True)
    assert ForbiddenDice.calls == 0


def test_replacement_in_a_plain_two_die_roll_is_refused():
    with pytest.raises(ValueError, match='ожидает одну к20'):
        replace_die(dict(dice=[10, 3], selected=10, bonus=2, total=12, mode='обычный'), 1, 7)


def test_dnd2014_has_no_heroic_inspiration_resource():
    rules = RulesetEngine(BUILTINS['dnd2014'])
    state = dict(hero=Hero(name='Человек', species='human').model_dump(),
                 ruleset=BUILTINS['dnd2014'].model_dump(), elapsed_minutes=5000)
    assert 'inspiration' not in rules.rest(state, 'long')
    assert 'heroic_inspiration' not in state['hero']['resources']
    engine_state, engine = battle(hero=Hero(name='Герой'))
    engine_state['ruleset'] = BUILTINS['dnd2014'].model_dump()
    with pytest.raises(ValueError, match='ресурс правил 2024'):
        CombatEngine(engine_state).gain_inspiration('hero')


def test_dagger_is_a_simple_weapon_like_the_catalog_says():
    hero = Hero(name='Герой', weapon_proficiencies=['martial'], abilities=dict(
        strength=14, dexterity=14, constitution=10, intelligence=10, wisdom=10, charisma=10))
    dagger_state, dagger = battle(11, 2, hero=hero, weapon='dagger')
    sword_state, sword = battle(11, 2, hero=hero, weapon='longsword')
    assert sword.attack('hero', 'foe')['roll']['bonus'] == dagger.attack('hero', 'foe')['roll']['bonus'] + 2


def test_voluntary_save_keeps_exhaustion_penalty_and_the_same_contract_keys():
    rules = RulesetEngine(BUILTINS['dnd2024'])
    hero = Hero(name='Истощённый', exhaustion=3)
    voluntary = rules.saving_throw(hero, 'dexterity', 15, voluntary=True)
    ordinary = rules.saving_throw(hero, 'dexterity', 15, rng=Dice(10))
    assert voluntary['bonus'] == ordinary['bonus']
    assert set(ordinary) - set(voluntary) == set()
    assert voluntary['draws'] == 0
