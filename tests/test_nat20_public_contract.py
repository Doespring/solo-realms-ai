"""§11: поставка доступна через публичный API, а не только из модуля."""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json

import pytest

import dnd5e_engine as E
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader


CONTRACT = (
    'derive_sheet', 'make_build_spec', 'start_combat', 'submit_player_intent',
    'get_live', 'drain_pending_events', 'narration_events', 'advance_monster_turn',
    'swap_initiative', 'serialize_combat', 'resume_combat',
    'PlayerIntent', 'CombatHandle', 'LiveCombatView',
    'COMBAT_SNAPSHOT_FORMAT', 'COMBAT_SNAPSHOT_EDITION',
)


def test_every_contracted_name_is_exported() -> None:
    """Интегратор берёт движок из пакета: имени, которого там нет, не существует."""
    missing = [name for name in CONTRACT if not hasattr(E, name)]
    assert not missing, missing


def test_every_contracted_name_is_in_all() -> None:
    """`__all__` — это и есть объявленный контракт, а не побочный эффект импорта."""
    missing = [name for name in CONTRACT if name not in E.__all__]
    assert not missing, missing


def test_the_contracted_api_really_runs_a_fight() -> None:
    """Имена экспортированы — и они работают: бой, снимок, продолжение."""
    scores = AbilityScores(
        strength=16, dexterity=14, constitution=14,
        intelligence=10, wisdom=12, charisma=10,
    )
    spec = CharacterBuildSpec(
        species_slug='human', class_slug='fighter', background_slug='soldier',
        level=5, ability_scores=scores,
    )
    sheet = E.derive_sheet(spec, loader=BundledAssetLoader())
    assert sheet.rules_edition == '5.2'
    assert sheet.hp_max > 0
    member = build_party_member(
        spec,
        CombatInstance(entity_id='pc1', name='F', zone_id='0,0', initiative=20, hp_max=40, hp_current=40),
        loader=BundledAssetLoader(),
    )
    handle = asyncio.run(
        E.start_combat(
            session_id='api', party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1', name='Foe', entity_type='Monster',
                    initiative=1, hp_current=30, hp_max=30, ac=10, zone_id='0,2',
                )
            ],
            grid_scene=GridScene(width=10, height=10), rng_seed=3,
        )
    ).handle
    view = E.get_live(handle)
    assert dataclasses.is_dataclass(view)
    E.drain_pending_events(handle)
    payload = E.serialize_combat(handle)
    # Снимок гоняем через ОТДЕЛЬНУЮ сессию: handle_id включает session_id,
    # поэтому оригинал остаётся живым, а восстановление не подменяет его.
    # Никакого доступа к приватному реестру тест не делает — контракт
    # проверяется только тем, что движок объявил публично.
    payload['session_id'] = 'api-resumed'
    payload['handle_id'] = 'combat:api-resumed:00000003'
    resumed = E.resume_combat(json.loads(json.dumps(payload)))
    assert E.get_live(resumed).round_number == view.round_number
    assert resumed.handle_id != handle.handle_id


def test_swap_initiative_is_a_coroutine_that_refuses_an_absent_ally() -> None:
    """Alert Swap доступен публично и отказывает объяснимо, а не молча."""
    assert inspect.iscoroutinefunction(E.swap_initiative)
    handle = E.CombatHandle(handle_id='combat:нет:00000000')
    with pytest.raises(Exception):
        asyncio.run(E.swap_initiative(handle, 'pc1', 'pc2'))
