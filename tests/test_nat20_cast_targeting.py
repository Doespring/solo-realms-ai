"""§9: отказ по цели наступает ДО расхода, а не после безрезультатного каста."""

from __future__ import annotations

import asyncio

import pytest

from dnd5e_engine import PlayerIntent, orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader


def _wizard(spells: tuple[str, ...]) -> object:
    spec = CharacterBuildSpec(
        species_slug='human', class_slug='wizard', background_slug='soldier', level=5,
        ability_scores=AbilityScores(
            strength=10, dexterity=14, constitution=14,
            intelligence=18, wisdom=12, charisma=10,
        ),
    )
    inst = CombatInstance(
        entity_id='pc1', name='W', zone_id='0,0', initiative=20,
        hp_max=30, hp_current=30, spells_known=spells,
    )
    return build_party_member(spec, inst, loader=BundledAssetLoader())


def _fight(tag: str):
    handle = asyncio.run(
        O.start_combat(
            session_id=tag, party=[_wizard(('flaming-sphere',))],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1', name='Foe', entity_type='Monster',
                    initiative=1, hp_current=40, hp_max=40, ac=10, zone_id='0,3',
                )
            ],
            grid_scene=GridScene(width=10, height=10), rng_seed=1,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _reasons(handle) -> list[str]:
    return [
        getattr(event, 'reason', '')
        for event in O.drain_pending_events(handle)
        if type(event).__name__ == 'CastFailed'
    ]


def test_naming_only_target_ids_refuses_before_spending_the_slot() -> None:
    """`target_ids` не читается резолвером: без отказа каст тратил слот впустую."""
    handle = _fight('targetids')
    before = dict(O._get_live(handle).spell_slots_by_entity['pc1'])
    asyncio.run(
        O.submit_player_intent(
            handle, 'pc1',
            PlayerIntent(
                intent_type='cast_spell', spell_id='flaming-sphere',
                activity_id='dnd5eactivity100', target_ids=('m1',), slot_level=2,
            ),
        )
    )
    live = O._get_live(handle)
    assert _reasons(handle) == ['target_invalid']
    assert dict(live.spell_slots_by_entity['pc1']) == before
    assert live.tracked_hp['m1'] == 40


def test_a_target_absent_from_the_fight_is_refused_before_spending() -> None:
    """Цель, названная в target_id, но не участвующая в бою, — тоже отказ."""
    handle = _fight('absent')
    before = dict(O._get_live(handle).spell_slots_by_entity['pc1'])
    asyncio.run(
        O.submit_player_intent(
            handle, 'pc1',
            PlayerIntent(
                intent_type='cast_spell', spell_id='flaming-sphere',
                activity_id='dnd5eactivity100', target_id='никого', slot_level=2,
            ),
        )
    )
    live = O._get_live(handle)
    assert _reasons(handle) == ['target_invalid']
    assert dict(live.spell_slots_by_entity['pc1']) == before
    assert live.tracked_hp['m1'] == 40


def test_a_named_target_still_casts_and_deals_its_damage() -> None:
    """Обратная сторона: отказ по цели не должен съесть законный каст."""
    handle = _fight('named')
    before = dict(O._get_live(handle).spell_slots_by_entity['pc1'])
    asyncio.run(
        O.submit_player_intent(
            handle, 'pc1',
            PlayerIntent(
                intent_type='cast_spell', spell_id='flaming-sphere',
                activity_id='dnd5eactivity100', target_id='m1', slot_level=2,
            ),
        )
    )
    live = O._get_live(handle)
    assert _reasons(handle) == []
    assert live.tracked_hp['m1'] < 40
    assert live.spell_slots_by_entity['pc1'][2] == before[2] - 1


def test_a_named_target_is_required_only_when_a_cast_names_one() -> None:
    """Заклинание без имени цели (щит-подобное) отказом не задевается."""
    from dnd5e_engine.orchestrator import _reject_untargeted_cast

    handle = _fight('selfbuff')
    live = O._get_live(handle)
    caster = next(c for c in live.initiative if c.entity_id == 'pc1')
    nameless = PlayerIntent(intent_type='cast_spell', spell_id='shield')
    assert _reject_untargeted_cast(live, caster, 'pc1', nameless) is False
