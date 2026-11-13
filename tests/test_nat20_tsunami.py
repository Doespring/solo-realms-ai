"""SRD 5.2: Tsunami — первый урон считается по заряду стены, а не по 6d10."""

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
# Первая активность (6d10 или половина) и вторая — (@item.uses.value)d10.
_FIRST = "XrJhKOVyHfLOkJ8b"
_RIDER = "o7MOuS6uL1ZV3dhe"


@pytest.fixture(autouse=True)
def _bundled_corpus():
    lib_loader.set_lib_loader_for_tests(_LOADER)
    yield
    lib_loader.set_lib_loader_for_tests(None)


def _wizard_fight():
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
            "spells_known": tuple(set(getattr(pc, "spells_known", ()) or ()) | {"tsunami"})
        }
    )
    result = asyncio.run(
        O.start_combat(
            session_id=f"tsunami-{next(_SESSION)}",
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


def _cast(handle, activity_id, **kwargs):
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            O.PlayerIntent(
                intent_type="cast_spell",
                spell_id="tsunami",
                activity_id=activity_id,
                slot_level=8,
                target_id="m1",
                **kwargs,
            ),
        )
    )
    return O.drain_pending_events(handle)


def test_the_rider_reads_the_spell_own_charge_instead_of_crashing():
    # Раньше публичный вызов падал ValueError: Unhandled roll-data token
    # "@item.uses.value" — формула второй активности не решалась вовсе.
    # Теперь она читает собственный заряд стены. Заряд не создан, и движок
    # называет это числом 0 (а не «неизвестно»): урон второй активности
    # равен 0 по каждому, кто под стеной, но событие урона всё равно есть —
    # это описанный исход, а не тишина.
    handle = _wizard_fight()
    events = _cast(handle, _RIDER)
    assert not [e for e in events if e.type == "cast_failed"], [e.type for e in events]
    damage = [e for e in events if e.type == "damage_applied"]
    assert damage, [e.type for e in events]
    assert [d.amount for d in damage] == [0], [d.amount for d in damage]

def test_the_first_save_activity_still_rolls_its_own_6d10():
    # Контроль: простая форма (6d10, половина при успехе) не должна съесть
    # токен пула — её урон 6..60 или половина.
    handle = _wizard_fight()
    events = _cast(handle, _FIRST)
    damage = [e for e in events if e.type == "damage_applied"]
    assert damage, [e.type for e in events]
    assert all(3 <= d.amount <= 60 for d in damage), [d.amount for d in damage]

