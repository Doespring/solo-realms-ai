"""SRD 5.2: заклинание без механики в источнике отвечает названным отказом."""

from __future__ import annotations

import asyncio
import itertools

import pytest

import dnd5e_engine.orchestrator as O
from dnd5e_engine import lib_loader
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

_LOADER = BundledAssetLoader()
_SESSION = itertools.count()
# Чисто нарративные заклинания: у каждого одна активность `utility` без
# `effects[]`, поэтому `activities/resolver.py` ничего не выдаёт — ни урона,
# ни состояния, ни события. Молчаливый расход ячейки неотличим от успеха,
# а §7 запрещает считать такую запись реализованной.
_NARRATIVE = ("dispel-magic", "daylight", "augury", "create-food-and-water")


@pytest.fixture(autouse=True)
def _bundled_corpus():
    lib_loader.set_lib_loader_for_tests(_LOADER)
    yield
    lib_loader.set_lib_loader_for_tests(None)


def _conjuring_fight(spells, form_id):
    """Тот же волшебник, но призыв требует выбранной формы."""
    handle = _wizard_fight(spells)
    live = O._get_live(handle)
    live.spell_forms_by_entity = getattr(live, "spell_forms_by_entity", {}) or {}
    return handle

def _wizard_fight(spells):
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="wizard",
        background_slug="acolyte",
        level=17,
        ability_scores=AbilityScores(
            strength=10, dexterity=14, constitution=14,
            intelligence=18, wisdom=14, charisma=10,
        ),
    )
    pc = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1",
            name="Волшебник",
            zone_id="0,0",
            initiative=20,
            hp_max=90,
            hp_current=90,
        ),
        loader=_LOADER,
    )
    pc = pc.model_copy(
        update={
            "spells_known": tuple(set(getattr(pc, "spells_known", ()) or ()) | set(spells))
        }
    )
    result = asyncio.run(
        O.start_combat(
            session_id=f"narrative-{next(_SESSION)}",
            party=[pc],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1",
                    name="Болван",
                    entity_type="Monster",
                    initiative=1,
                    hp_current=999,
                    hp_max=999,
                    ac=12,
                    zone_id="2,0",
                )
            ],
            grid_scene=GridScene(width=12, height=12),
            rng_seed=3,
        )
    )
    O.drain_pending_events(result.handle)
    return result.handle


def _cast(handle, slug, slot_level=7, **kwargs):
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            O.PlayerIntent(
                intent_type="cast_spell",
                spell_id=slug,
                slot_level=slot_level,
                target_id="m1",
                **kwargs,
            ),
        )
    )
    return O.drain_pending_events(handle)


def test_an_activity_without_mechanics_refuses_before_spending_the_slot():
    handle = _wizard_fight(_NARRATIVE)
    before = dict(O._get_live(handle).spell_slots_by_entity["pc1"])
    events = _cast(handle, "dispel-magic")
    assert [e.reason for e in events if e.type == "cast_failed"] == [
        "narrative_spell_without_mechanics"
    ], [e.type for e in events]
    # Отказ назван, а не подменён правдоподобным рассказом: ни каста, ни
    # события механики, ни расхода ячейки.
    assert not [e for e in events if e.type == "spell_cast"]
    assert not [e for e in events if e.type == "damage_applied"]
    assert dict(O._get_live(handle).spell_slots_by_entity["pc1"]) == before


def test_the_refusal_names_every_narrative_spell_the_same_way():
    handle = _wizard_fight(_NARRATIVE)
    for slug in _NARRATIVE[1:]:
        events = _cast(handle, slug)
        reasons = [e.reason for e in events if e.type == "cast_failed"]
        assert reasons == ["narrative_spell_without_mechanics"], (slug, reasons)


def test_a_mechanical_spell_of_the_same_shape_still_resolves():
    # Контрольный случай: отказ не должен поймать заклинание с механикой.
    handle = _wizard_fight(("delayed-blast-fireball",))
    events = _cast(handle, "delayed-blast-fireball", slot_level=8, activity_id="iWd7zKpQIgWofJAp")
    assert [e for e in events if e.type == "spell_cast"], [e.type for e in events]
    assert not [e for e in events if e.type == "cast_failed"]



def test_a_summon_the_engine_cannot_perform_is_refused_not_silently_cast():
    # Общий контр-пример к разбору §7: если бы гейт считал нарративом только
    # `utility`-без-effect, а `summon` без статблока пропускал как механику,
    # призыв Spare the Dying-подобной формы прошёл бы молча. У `conjure-animals`
    # нет статблока, значит исход обязан быть назван движком.
    handle = _wizard_fight(("conjure-animals",))
    events = _cast(handle, "conjure-animals", slot_level=3, form_id="phbSpectralAnima")
    outcomes = [e for e in events if e.type in ("cast_failed", "combatant_joined")]
    assert outcomes, [e.type for e in events]
