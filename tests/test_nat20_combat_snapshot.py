"""§9: бой переживает сохранение и продолжается с того же места."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import random
from typing import Any

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader


def _fighter() -> Any:
    scores = AbilityScores(
        strength=16, dexterity=14, constitution=14,
        intelligence=10, wisdom=12, charisma=10,
    )
    spec = CharacterBuildSpec(
        species_slug='human', class_slug='fighter', background_slug='soldier',
        level=5, ability_scores=scores,
    )
    return build_party_member(
        spec,
        CombatInstance(
            entity_id='pc1', name='Fighter', zone_id='0,0', initiative=20,
            hp_max=40, hp_current=40,
        ),
        loader=BundledAssetLoader(),
    )


def _fight(tag: str) -> Any:
    handle = asyncio.run(
        O.start_combat(
            session_id=tag,
            party=[_fighter()],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1', name='Foe', entity_type='Monster',
                    initiative=1, hp_current=40, hp_max=40, ac=10, zone_id='0,3',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=5,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def test_a_snapshot_round_trips_through_json_unchanged() -> None:
    """Снимок — обычные данные: JSON-круг ничего не теряет и не добавляет."""
    handle = _fight('snap-json')
    payload = O.serialize_combat(handle)
    assert json.loads(json.dumps(payload)) == payload


def test_a_restored_fight_keeps_spent_resources_and_the_turn() -> None:
    """§9: восстановленный бой стоит там же, где сохранённый."""
    handle = _fight('snap-resume')
    asyncio.run(
        O.submit_player_intent(
            handle, 'pc1',
            O.PlayerIntent(intent_type='use_feature', feature_id='second-wind'),
        )
    )
    O.drain_pending_events(handle)
    before = O._get_live(handle)
    blob = json.dumps(O.serialize_combat(handle))
    # Исходный бой отпускаем: handle_id уникален, повторный захват — ошибка.
    O._REGISTRY.pop(handle.handle_id, None)
    restored = O.resume_combat(json.loads(blob))
    after = O._get_live(restored)
    assert after.custom_counters_by_entity['pc1']['feature_use:second-wind']['spent'] == 1
    assert after.current_turn_index == before.current_turn_index
    assert after.round_number == before.round_number
    assert after.current_actor_id == before.current_actor_id
    assert {c.entity_id: c.hp_current for c in after.initiative} == {
        c.entity_id: c.hp_current for c in before.initiative
    }
    assert after.spell_slots_by_entity == before.spell_slots_by_entity
    assert after.active_conditions == before.active_conditions


def test_the_restored_fight_continues_with_the_same_die_stream() -> None:
    """§9: возобновление не перезапускает ГСЧ — следующий бросок тот же."""
    handle = _fight('snap-rng')
    live = O._get_live(handle)
    # Прокручиваем поток, чтобы его состояние точно не равнялось seed.
    for _ in range(7):
        live.rng.random()
    blob = json.dumps(O.serialize_combat(handle))
    expected = live.rng.random()
    O._REGISTRY.pop(handle.handle_id, None)
    restored = O._get_live(O.resume_combat(json.loads(blob)))
    assert restored.rng.random() == expected


def test_restored_slot_pools_keep_their_integer_level_keys() -> None:
    """JSON превращает уровень слота в строку; ключ обязан остаться int."""
    handle = _fight('snap-slots')
    live = O._get_live(handle)
    live.spell_slots_by_entity['pc1'] = {1: 2}
    live.pact_slots_by_entity['pc1'] = {2: 1}
    blob = json.dumps(O.serialize_combat(handle))
    O._REGISTRY.pop(handle.handle_id, None)
    restored = O._get_live(O.resume_combat(json.loads(blob)))
    assert restored.spell_slots_by_entity['pc1'] == {1: 2}
    assert restored.pact_slots_by_entity['pc1'] == {2: 1}
    assert list(restored.spell_slots_by_entity['pc1']) == [1]

def test_a_snapshot_of_another_format_version_is_refused() -> None:
    """§10: чужой формат снимка не переинтерпретируется под текущими правилами."""
    payload = O.serialize_combat(_fight('snap-fmt'))
    payload['snapshot_format'] = O.COMBAT_SNAPSHOT_FORMAT + 1
    with pytest.raises(ValueError, match='snapshot format'):
        O.resume_combat(payload)


def test_a_snapshot_without_a_format_version_is_refused() -> None:
    """Старый снимок без версии — тоже отказ, а не догадка о его смысле."""
    payload = O.serialize_combat(_fight('snap-old'))
    payload.pop('snapshot_format')
    with pytest.raises(ValueError, match='snapshot format'):
        O.resume_combat(payload)


def test_a_snapshot_of_another_rules_edition_is_refused() -> None:
    """§2/§10: материал другой редакции не подменяется правилами 5.2."""
    payload = O.serialize_combat(_fight('snap-ed'))
    payload['rules_edition'] = '5.1'
    with pytest.raises(ValueError, match='rules edition'):
        O.resume_combat(payload)


def test_a_snapshot_records_the_edition_it_was_computed_under() -> None:
    """Снимок объявляет редакцию, а не наследует её молча."""
    payload = O.serialize_combat(_fight('snap-ed2'))
    assert payload['rules_edition'] == O.COMBAT_SNAPSHOT_EDITION == '5.2'
    assert payload['snapshot_format'] == O.COMBAT_SNAPSHOT_FORMAT
