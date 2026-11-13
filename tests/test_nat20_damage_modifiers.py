"""Требование 6: сопротивления, уязвимости и иммунитеты (§ «Damage» SRD 5.2).

Порядок по корпусу (`activities/apply.py:6`): уязвимость ×2 → сопротивление //2
(целочисленное деление) → иммунитет ⇒ 0.

Дефект найден отрицательным примером: уязвимость 20 огня не удваивалась.
Строка 124 читала ТОЛЬКО `sidecar["vulnerabilities"]`, тогда как сопротивления
(стр. 122) и иммунитеты (стр. 123) объединяли статическое поле цели с sidecar.
Поле `Combatant.damage_vulnerabilities` существовало, но в расчёт не входило —
ровно то, что п.7 запрещает считать реализованным.
"""
import random

from dnd5e_engine.activities.apply import apply_damage
from dnd5e_engine.activities.context import ActivityResolutionContext
from dnd5e_engine.types import Combatant

BASE = dict(
    entity_id="m1", name="Dummy", entity_type="Monster", initiative=1,
    hp_current=50, hp_max=50, ac=12, zone_id="1,0",
)


def _combatant(**overrides):
    return Combatant(**{**BASE, **overrides})


def _context():
    return ActivityResolutionContext(
        rng=random.Random(1),
        caster=_combatant(entity_id="m0", zone_id="0,0"),
        targets=[],
        event_emitter=lambda event: None,
        caster_abilities={},
    )


def _dealt(target, amount=20, damage_type="fire"):
    return apply_damage(target, {damage_type: amount}, _context())


def test_a_plain_target_takes_the_full_amount():
    assert _dealt(_combatant()) == 20


def test_resistance_halves():
    assert _dealt(_combatant(damage_resistances=("fire",))) == 10


def test_vulnerability_doubles():
    """Строка 124 не читала `Combatant.damage_vulnerabilities` — дефект закрыт."""
    dealt = _dealt(_combatant(damage_vulnerabilities=("fire",)))
    assert dealt == 40, f"уязвимость к огню не удвоила урон: {dealt}, ожидалось 40"


def test_immunity_zeroes():
    assert _dealt(_combatant(damage_immunities=("fire",))) == 0


def test_a_different_damage_type_is_unaffected():
    assert _dealt(_combatant(damage_vulnerabilities=("fire",)), damage_type="cold") == 20


def test_the_srd_order_applies():
    """Уязвимость ×2 → сопротивление //2: 20 → 40 → 20."""
    both = _combatant(
        damage_vulnerabilities=("fire",), damage_resistances=("fire",)
    )
    assert _dealt(both) == 20

