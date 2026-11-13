"""Сквозная проверка: заклинание со спасброском реально накладывает состояние.

Пункт 7 запрещает считать способность реализованной потому, что JSON загружается,
запись есть в списке или обработчик вернул успех. Здесь проверяется ФАКТИЧЕСКОЕ
изменение состояния: берется настоящее заклинание из корпуса, настоящий
`apply_activity_effects` и проверяется, что при провале спасброска состояние
появляется, а при успехе — нет.

Выбран `hold-person`: у него нет урона, поэтому единственный наблюдаемый исход —
состояние `paralyzed`. Если бы механизм молча не срабатывал, изменение состояния
было бы неотличимо от успешного спасброска, и такой тест обязателен.
"""
import random

import pytest

from dnd5e_engine.activities.context import ActivityResolutionContext, Combatant
from dnd5e_engine.activities.effects import apply_activity_effects
from dnd5e_srd_data.loader import BundledAssetLoader


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def _combatant(entity_id, *, ac=12, hp=20):
    return Combatant(
        entity_id=entity_id,
        entity_type="Monster",
        name=entity_id,
        initiative=10,
        hp_current=hp,
        hp_max=hp,
        ac=ac,
    )


def _cast(loader, slug, *, save_succeeded, cast_level=3):
    """Разрешить первое активное действие заклинания по настоящим данным корпуса."""
    spell = loader.get_spell(slug)
    activity = spell.activities[0]
    caster = _combatant("caster")
    target = _combatant("target")
    events = []
    ctx = ActivityResolutionContext(
        rng=random.Random(7),
        caster=caster,
        targets=[target],
        event_emitter=events.append,
        caster_abilities={
            "str": 10, "dex": 12, "con": 14, "int": 16, "wis": 12, "cha": 10,
        },
        caster_level=5,
        spellcasting_ability="int",
        source_passive_effects=tuple(spell.passive_effects or ()),
        concentration=bool(spell.concentration),
    )
    apply_activity_effects(
        activity,
        ctx,
        target,
        save_succeeded=save_succeeded,
        cast_level=cast_level,
    )
    return events


def _conditions(events):
    out = []
    for event in events:
        kind = getattr(event, "kind", None) or type(event).__name__
        if "condition" in str(kind).lower():
            payload = getattr(event, "payload", None) or {}
            value = payload.get("condition") if isinstance(payload, dict) else None
            out.append(value or getattr(event, "condition", None))
    return [c for c in out if c]


def test_hold_person_paralyzes_on_a_failed_save(loader):
    events = _cast(loader, "hold-person", save_succeeded=False)
    assert _conditions(events) == ["paralyzed"]


def test_hold_person_does_not_paralyze_on_a_successful_save(loader):
    events = _cast(loader, "hold-person", save_succeeded=True)
    assert _conditions(events) == [], "успешный спасбросок обязан отменить состояние"


def test_blindness_deafness_applies_both_conditions(loader):
    events = _cast(loader, "blindness-deafness", save_succeeded=False)
    assert sorted(_conditions(events)) == ["blinded", "deafened"]


def test_a_damage_spell_still_applies_its_condition(loader):
    """`black-tentacles` накладывает `restrained`; проверяем не-пустой исход."""
    events = _cast(loader, "black-tentacles", save_succeeded=False, cast_level=3)
    assert "restrained" in _conditions(events)

