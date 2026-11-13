"""SRD 5.2: Revivify возвращает существо с 1 хитом — это лечение, не урон."""

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


@pytest.fixture(autouse=True)
def _bundled_corpus():
    lib_loader.set_lib_loader_for_tests(_LOADER)
    yield
    lib_loader.set_lib_loader_for_tests(None)


def _cleric_fight(*, fallen_ally: bool):
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="cleric",
        background_slug="acolyte",
        level=17,
        ability_scores=AbilityScores(
            strength=10, dexterity=12, constitution=14,
            intelligence=10, wisdom=18, charisma=12,
        ),
    )
    cleric = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1",
            name="Клирик",
            zone_id="0,0",
            initiative=20,
            hp_max=90,
            hp_current=90,
        ),
        loader=_LOADER,
    )
    cleric = cleric.model_copy(
        update={
            "spells_known": tuple(set(getattr(cleric, "spells_known", ()) or ()) | {"revivify"})
        }
    )
    party = [cleric]
    if fallen_ally:
        ally_spec = CharacterBuildSpec(
            species_slug="human",
            class_slug="fighter",
            background_slug="acolyte",
            level=5,
            ability_scores=AbilityScores(
                strength=16, dexterity=12, constitution=14,
                intelligence=10, wisdom=10, charisma=10,
            ),
        )
        party.append(
            build_party_member(
                ally_spec,
                CombatInstance(
                    entity_id="pc2",
                    name="Павший",
                    zone_id="1,0",
                    initiative=5,
                    hp_max=40,
                    hp_current=0,
                ),
                loader=_LOADER,
            )
        )
    handle = asyncio.run(
        O.start_combat(
            session_id=f"revivify-{next(_SESSION)}",
            party=party,
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1",
                    name="Болван",
                    entity_type="Monster",
                    initiative=1,
                    hp_current=999,
                    hp_max=999,
                    ac=12,
                    zone_id="4,0",
                )
            ],
            grid_scene=GridScene(width=12, height=12),
            rng_seed=3,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _cast(handle, target_id):
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            O.PlayerIntent(
                intent_type="cast_spell",
                spell_id="revivify",
                activity_id="dnd5eactivity000",
                slot_level=3,
                target_id=target_id,
            ),
        )
    )
    return O.drain_pending_events(handle)


def test_the_restored_hit_point_is_a_heal_not_a_dropped_damage_part():
    # Корпус записывает «возвращается с 1 хитом» как активность `damage`
    # с единственной частью типа `healing`. Раньше эта часть доходила до
    # apply_damage как damage_type_invalid и выбрасывалась: ячейка тратилась,
    # событий лечения не было вовсе.
    handle = _cleric_fight(fallen_ally=True)
    events = _cast(handle, "pc2")
    assert not [e for e in events if e.type == "cast_failed"], [e.type for e in events]
    healed = [e for e in events if e.type == "healing_applied"]
    assert healed, [e.type for e in events]
    assert healed[0].target_id == "pc2"
    assert healed[0].amount == 1
    live = O._get_live(handle)
    ally = next(c for c in live.initiative if c.entity_id == "pc2")
    assert ally.hp_current == 1, ally.hp_current
    assert ally.is_alive, "оживлённый обязан быть живым"


def test_no_damage_event_is_invented_for_the_restoration():
    handle = _cleric_fight(fallen_ally=True)
    events = _cast(handle, "pc2")
    assert not [e for e in events if e.type == "damage_applied"], [e.type for e in events]
