"""Требование 6: падение до нуля и спасброски от смерти (SRD 5.2 §Dying).

Правило:
- натуральная 20 — «you regain 1 hit point», персонаж встаёт, остаётся Prone;
- натуральная 1 — два провала;
- обычный успех — один успех; три успеха — стабилизация; три провала — смерть.

Урок этого набора: первая версия проверки смотрела только на счётчики
`death_saves` и объявила дефектом «нат.20 не возвращает хиты». Факт показал
`hp_current: 1`, `is_alive: True`, `prone` — правило работало. Четвёртая ложная
тревога сессии по той же причине: проверка не того поля.
"""
import random

import pytest

from dnd5e_engine import death_saves as D
from dnd5e_engine.types.combat import Combatant


class _Fixed(random.Random):
    """d20 с заданным результатом — воспроизводимость без подбора зерна."""

    def __init__(self, face):
        super().__init__(0)
        self.face = face

    def randint(self, a, b):
        return self.face


def _dying():
    return Combatant(
        entity_id="pc1", entity_type="Character", name="Fighter",
        initiative=20, hp_current=0, hp_max=12,
    )


def test_a_natural_twenty_returns_the_character_to_one_hit_point():
    """SRD 5.2: «If you roll a 20 on the d20, you regain 1 Hit Point.»"""
    result = D.roll_death_save(_dying(), _Fixed(20))
    assert result.outcome == "critical_success"
    assert result.combatant.hp_current == 1, "нат.20 не вернула хит"
    assert result.combatant.is_alive is True
    assert result.combatant.death_saves["successes"] == 0
    assert result.combatant.death_saves["failures"] == 0


def test_the_revived_character_is_prone():
    """SRD 5.2 Unconscious: «When this condition ends, you remain Prone.»"""
    result = D.roll_death_save(_dying(), _Fixed(20))
    conditions = [c.condition for c in result.combatant.conditions]
    assert "prone" in conditions, conditions
    assert "unconscious" not in conditions, conditions


def test_a_natural_one_costs_two_failures():
    result = D.roll_death_save(_dying(), _Fixed(1))
    assert result.combatant.death_saves["failures"] == 2
    assert result.combatant.hp_current == 0


def test_an_ordinary_success_counts_once():
    result = D.roll_death_save(_dying(), _Fixed(10))
    assert result.combatant.death_saves["successes"] == 1
    assert result.combatant.death_saves["failures"] == 0
    assert result.combatant.hp_current == 0


def test_three_failures_kill():
    """Три провала — смерть; необратимо в пределах этого вызова."""
    dying = _dying()
    dying = D.roll_death_save(dying, _Fixed(1)).combatant   # 2 провала
    result = D.roll_death_save(dying, _Fixed(5))            # 3-й
    assert result.outcome == "dead", result.outcome
    assert result.combatant.is_alive is False


def test_three_successes_stabilise():
    dying = _dying()
    for _ in range(3):
        dying = D.roll_death_save(dying, _Fixed(10)).combatant
    result = D.roll_death_save(dying, _Fixed(10))
    assert dying.death_saves["is_stable"] is True, dying.death_saves
    assert dying.hp_current == 0

