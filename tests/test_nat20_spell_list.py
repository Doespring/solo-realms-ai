"""Список заклинаний персонажа действительно ограничивает каст (п.5, 7).

`live.spells_known_by_entity` читался ТОЛЬКО для наратива: `cast_spell`
разрешался по корпусу, поэтому персонаж без Bless в списке всё равно его
кастовал (`spell_cast`, `effect_applied`). Совет хоста при этом честно
отчитывался о дефекте, но движок его не закрывал — правило 7 требует
проверять фактическое изменение состояния, а не текст отчёта.

Отказ стоит в `pre_resolution_gates`, до траты слота (§9). Пустой список
считается неизвестным: движок не отнимает возможность, о которой его не
проинформировали.
"""
import asyncio

from dnd5e_engine import (
    GridScene,
    PlayerIntent,
    drain_pending_events,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.orchestrator import _get_live
from dnd5e_engine.specs import EncounterMemberSpec, PartyMemberSpec
from dnd5e_srd_data.loader import BundledAssetLoader


def _fighter(*, known):
    loader = BundledAssetLoader()
    hero = PartyMemberSpec.model_validate({
        "entity_id": "hero", "name": "Hero", "hp_current": 10, "hp_max": 10,
        "ac": 11, "initiative": 20, "zone_id": "1,1",
        "spells_known": list(known), "spell_slots": {1: 3},
    })
    foe = EncounterMemberSpec(
        entity_id="foe", entity_type="Monster", name="Foe", initiative=1,
        hp_current=10, hp_max=10, ac=15, attack_bonus=4,
        damage_dice="1d6", damage_type="slashing", zone_id="2,1", base_speed=0,
    )
    import dnd5e_engine
    dnd5e_engine.configure_lib_loader(loader)
    handle = asyncio.run(start_combat(
        session_id="s", party=[hero], encounter=[foe],
        grid_scene=GridScene(width=5, height=5), rng_seed=7,
    )).handle
    drain_pending_events(handle)
    return handle


def _cast(handle, spell="bless"):
    asyncio.run(submit_player_intent(
        handle, "hero",
        PlayerIntent(intent_type="cast_spell", spell_id=spell, target_id="hero", slot_level=1),
    ))
    return drain_pending_events(handle)


def _slots(handle):
    return dict(_get_live(handle).spell_slots_by_entity.get("hero", {}))


def test_a_prepared_spell_casts():
    events = _cast(_fighter(known=["bless"]))
    assert [e.type for e in events] == ["intent_submitted", "spell_cast", "effect_applied"]


def test_a_spell_outside_the_list_is_refused_by_name():
    events = _cast(_fighter(known=["cure-wounds"]))
    assert [(e.type, e.reason) for e in events] == [(
        ("cast_failed", "spell_not_available")
    )], [(e.type, getattr(e, "reason", None)) for e in events]


def test_a_refused_spell_spends_neither_slot_nor_action():
    handle = _fighter(known=["cure-wounds"])
    before = _slots(handle)
    _cast(handle)
    assert _slots(handle) == before
    hero = next(c for c in _get_live(handle).initiative if c.entity_id == "hero")
    assert hero.action_available is True


def test_an_uninformed_list_leaves_the_caster_alone():
    """Пустой список — «движок не знает», а не «ничего нельзя»."""
    events = _cast(_fighter(known=[]))
    assert "spell_cast" in [e.type for e in events]
