"""Требование 6: критические попадания удваивают КОСТИ, а не модификатор.

Правило SRD 5.2: «roll all of the attack's damage dice twice and add them
together, then add any relevant modifiers as normal».

Проверяется детерминированно: `random.Random` с `randint`, возвращающим ОДНО
лицо, даёт точное ожидание. Обычный бросок `2d6+3` при лице 4 — 11; под
критом — `4d6+3` = 19, а НЕ удвоение итога 22 и не удвоение модификатора.
"""
import random

from dnd5e_engine.activities.dice import roll_damage_part
from dnd5e_srd_data.loader import BundledAssetLoader


class _Fixed(random.Random):
    """Бросок с заранее известным лицом — без подбора зерна."""

    def __init__(self, face):
        super().__init__(0)
        self.face = face

    def randint(self, a, b):
        _ = (a, b)
        return self.face


def _part():
    weapon = BundledAssetLoader().get_weapon("greatsword")
    return weapon.damage_parts[0]


def test_a_crit_doubles_the_dice_and_not_the_modifier():
    part = _part()
    normal = roll_damage_part(part, _Fixed(4), crit=False)
    crit = roll_damage_part(part, _Fixed(4), crit=True)
    # 2d6 (лицо 4) = 8 без модификатора; крит = 4d6 = 16.
    assert normal == 8, f"обычный урон 2d6 при лице 4 = {normal}, ожидалось 8"
    assert crit == 16, (
        f"крит 2d6 при лице 4 = {crit}, ожидалось 16: "
        "удваиваются КОСТИ, а не итог"
    )


def test_without_crit_the_dice_are_not_doubled():
    part = _part()
    assert roll_damage_part(part, _Fixed(1), crit=False) == 2
    assert roll_damage_part(part, _Fixed(1), crit=True) == 4

