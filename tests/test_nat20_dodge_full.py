"""SRD 5.2: полный эффект Уклонения — обе половины, а не только помеха.

Матрица возможностей апстрима (tapestria.github.io/nat20/capabilities) держит
"Attack rolls, crits, damage..." в Partial именно из-за этой дырки: помеха на
атаку по уклоняющемуся (attack roll) есть, а преимущество на его спасброски
Ловкости — нет. Боевой код хоста `CombatEngine` знал только первую половину.

Проверяем то, что «уклонение» превращается в РЕЗУЛЬТАТ броска, а не в флаг:
1. преимущество на спасбросок Ловкости;
2. бонус теряется при недееспособности;
3. бонус теряется при скорости 0;
4. помеха на атаку по уклоняющемуся остаётся (не сломали существующее);
5. уклонение снимается в начале следующего хода.
"""
from copy import deepcopy

import pytest

from solo.combat import Combatant, CombatEngine
from solo.contracts import Hero
from solo.geometry import Position, rectangular_map
from solo.rulesets import BUILTINS


class Dice:
    """Кубик с заранее заданной последовательностью; проверяет границы."""

    def __init__(self, *values):
        self.values = list(values)
        self.calls = []

    def randint(self, low, high):
        assert self.values, 'кубиков больше не осталось'
        value = self.values.pop(0)
        assert low <= value <= high, (low, value, high)
        self.calls.append((low, high, value))
        return value


def battle(*draws, enemy_position=(2, 1)):
    hero = Combatant(id='hero', stats=Hero(name='Hero', weapon_proficiencies=['martial']),
                     position=Position(x=1, y=1), team='hero', weapon='longsword', ammo=5)
    foe = Combatant(id='foe', stats=Hero(name='Foe', hp=100, max_hp=100, ac=10),
                    position=Position(x=enemy_position[0], y=enemy_position[1]),
                    team='enemy', weapon='club')
    state = {'hero': hero.stats.model_dump(), 'ruleset': BUILTINS['dnd2024'].model_dump(),
             'combat': {'active': True, 'map': rectangular_map(20, 6).model_dump(),
                        'actors': [hero.model_dump(), foe.model_dump()],
                        'order': ['hero', 'foe'], 'cursor': 0, 'round': 1,
                        'lethal_risk_declared': True}}
    return CombatEngine(state, rng=Dice(*draws))


def test_dodging_grants_advantage_on_dexterity_saves():
    engine = battle()
    hero = engine.actors['hero']
    engine.defend('hero', 'dodge')
    # Преимущество: берутся два d20, остаётся больший.
    saves = engine.dexterity_save_advantage()
    assert saves == ['hero'], saves


def test_dodge_benefit_is_lost_when_incapacitated():
    engine = battle()
    hero = engine.actors['hero']
    engine.defend('hero', 'dodge')
    hero.stats.conditions = ['incapacitated']
    assert engine.dexterity_save_advantage() == []


def test_dodge_benefit_is_lost_when_speed_is_zero():
    engine = battle()
    hero = engine.actors['hero']
    engine.defend('hero', 'dodge')
    hero.stats.conditions = ['restrained']  # Скован: скорость 0
    assert engine.dexterity_save_advantage() == []


def test_dodge_benefit_expires_at_the_start_of_the_next_turn():
    engine = battle()
    engine.defend('hero', 'dodge')
    assert engine.dexterity_save_advantage() == ['hero']
    engine.end_turn('hero')
    engine.end_turn('foe')
    assert engine.current().id == 'hero'
    assert engine.dexterity_save_advantage() == []


def test_attacks_against_a_dodging_actor_still_roll_with_disadvantage():
    # Атакующий бросает два d20 и берёт меньший (помеха), затем урон.
    # Ход героя: он уклоняется, передаёт ход врагу, тот атакует.
    engine = battle(18, 3, 5)
    engine.defend('hero', 'dodge')
    engine.end_turn('hero')
    assert engine.current().id == 'foe'
    result = engine.attack('foe', 'hero')
    assert result['roll']['dice'] == [18, 3]
    assert result['roll']['selected'] == 3
    assert result['roll']['mode'] == 'помеха'


def test_the_dodge_reason_is_named_in_the_save_advantage():
    """Хост должен объяснять, ОТКУДА преимущество, а не только что оно есть."""
    engine = battle()
    engine.defend('hero', 'dodge')
    reasons = engine.dexterity_save_advantage(reasons=True)
    assert reasons == {'hero': ['уклонение']}
