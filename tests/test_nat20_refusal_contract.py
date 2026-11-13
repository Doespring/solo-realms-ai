"""Приёмка §5/§9: отказ виден игроку и не списывает ресурс."""

from __future__ import annotations

import asyncio
from typing import Any

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader


def _wizard(level: int = 3) -> Any:
    scores = AbilityScores(
        strength=8, dexterity=14, constitution=12,
        intelligence=16, wisdom=10, charisma=10,
    )
    spec = CharacterBuildSpec(
        species_slug='human', class_slug='wizard', background_slug='soldier',
        level=level, ability_scores=scores,
        selected_choices=('cantrip:fire-bolt', 'spell:magic-missile'),
    )
    return build_party_member(
        spec,
        CombatInstance(
            entity_id='pc1', name='Wizard', zone_id='0,0', initiative=20,
            hp_max=20, hp_current=20,
        ),
        loader=BundledAssetLoader(),
    )


def _fighter(hp: int = 40) -> Any:
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
            hp_max=40, hp_current=hp,
        ),
        loader=BundledAssetLoader(),
    )


def _fight(member: Any, tag: str, hp: int = 40) -> Any:
    handle = asyncio.run(
        O.start_combat(
            session_id=tag,
            party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1', name='Foe', entity_type='Monster',
                    initiative=1, hp_current=hp, hp_max=hp, ac=10, zone_id='0,3',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=5,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _submit(handle: Any, intent: Any) -> list[Any]:
    asyncio.run(O.submit_player_intent(handle, 'pc1', intent))
    return O.drain_pending_events(handle)


def _reason(events: list[Any]) -> str:
    failures = [e for e in events if type(e).__name__ == 'CastFailed']
    assert failures, f'отказ прошёл молча: {[type(e).__name__ for e in events]}'
    return failures[0].reason


def test_a_cantrip_cannot_be_cast_with_a_slot() -> None:
    """§6: заговор без слота; запрос слота отвергается и слот цел."""
    handle = _fight(_wizard(), 'cantrip-slot')
    _fight_slots = dict(O._get_live(handle).spell_slots_by_entity['pc1'])
    events = _submit(
        handle,
        O.PlayerIntent(
            intent_type='cast_spell', spell_id='fire-bolt', slot_level=1, target_id='m1',
        ),
    )
    assert _reason(events) == 'no_slot'
    slots = O._get_live(handle).spell_slots_by_entity['pc1']
    assert slots == dict(_fight_slots), f'слот потрачен: {slots}'


def test_repeating_a_cast_neither_spends_a_slot_nor_deals_damage_again() -> None:
    """§9: повтор не списывает ресурс и не наносит урон второй раз."""
    handle = _fight(_wizard(), 'repeat-cast')
    intent = O.PlayerIntent(
        intent_type='cast_spell', spell_id='magic-missile', slot_level=1, target_id='m1',
    )
    _submit(handle, intent)
    live = O._get_live(handle)
    slots_after_first = dict(live.spell_slots_by_entity['pc1'])
    hp_after_first = [c.hp_current for c in live.initiative if c.entity_id == 'm1'][0]
    events = _submit(handle, intent)
    assert _reason(events) == 'no_action_economy'
    live = O._get_live(handle)
    assert live.spell_slots_by_entity['pc1'] == slots_after_first
    assert [c.hp_current for c in live.initiative if c.entity_id == 'm1'][0] == hp_after_first


def test_repeating_a_feature_neither_heals_again_nor_spends_again() -> None:
    """§9: повторное применение признака не лечит и не тратит второй раз."""
    handle = _fight(_fighter(hp=10), 'repeat-wind', hp=30)
    intent = O.PlayerIntent(intent_type='use_feature', feature_id='second-wind')
    _submit(handle, intent)
    live = O._get_live(handle)
    spent_after_first = dict(live.custom_counters_by_entity['pc1'])
    hp_after_first = [c.hp_current for c in live.initiative if c.entity_id == 'pc1'][0]
    events = _submit(handle, intent)
    assert _reason(events) == 'no_action_economy'
    live = O._get_live(handle)
    assert live.custom_counters_by_entity['pc1'] == spent_after_first
    assert [c.hp_current for c in live.initiative if c.entity_id == 'pc1'][0] == hp_after_first


def test_an_exhausted_feature_use_is_announced() -> None:
    """§5: исчерпанные применения отвергаются с причиной."""
    handle = _fight(_fighter(hp=10), 'wind-exhausted', hp=30)
    live = O._get_live(handle)
    live.custom_counters_by_entity.setdefault('pc1', {})['feature_use:second-wind'] = {
        'spent': 3
    }
    events = _submit(handle, O.PlayerIntent(intent_type='use_feature', feature_id='second-wind'))
    assert _reason(events) == 'no_uses_remaining'
