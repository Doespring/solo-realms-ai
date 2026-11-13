"""Find Familiar: a legal form outside the printed list reaches the DM.

SRD 5.2 Find Familiar: "a spirit that takes an animal form you choose: Bat,
Cat, Frog, Hawk, Lizard, Octopus, Owl, Rat, Raven, Spider, Weasel, or another
Beast that has a Challenge Rating of 0." The trailing clause names the SET of
legal forms and leaves WHICH one to the table — the same kind of fact as
Create Undead's "only at night". Before this file the engine refused such a
cast with ``cast_failed/reason=target_invalid``, which named the wrong cause:
the target was legal, the world's answer was missing.

The tests below pin the three halves of the contract: the question is asked
and nothing is spent; the DM's answer seats the creature; a form the book
does NOT allow is still refused, with no round trip that could only end in
the same refusal.
"""

from __future__ import annotations

import asyncio

import dnd5e_engine.orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

ANSWER_KEY = 'find-familiar:form'


def _fight():
    loader = BundledAssetLoader()
    spec = CharacterBuildSpec(
        species_slug='human',
        class_slug='wizard',
        background_slug='acolyte',
        level=1,
        ability_scores=AbilityScores(
            strength=10,
            dexterity=14,
            constitution=14,
            intelligence=16,
            wisdom=12,
            charisma=10,
        ),
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id='pc1',
            name='P',
            zone_id='0,0',
            initiative=20,
            hp_max=8,
            hp_current=8,
        ),
        loader=loader,
    ).model_copy(update={'spells_known': ('find-familiar',), 'spells_prepared': ('find-familiar',)})
    handle = asyncio.run(
        O.start_combat(
            session_id='find-familiar-dm',
            party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1',
                    name='Foe',
                    entity_type='Monster',
                    initiative=1,
                    hp_current=9,
                    hp_max=9,
                    ac=12,
                    zone_id='0,1',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=5,
        )
    ).handle
    O.drain_pending_events(handle)
    activity = loader.get_spell('find-familiar').activities[0]
    return handle, activity.id


def _cast(handle, activity_id: str, form: str, **extra):
    asyncio.run(
        O.submit_player_intent(
            handle,
            'pc1',
            O.PlayerIntent(
                intent_type='cast_spell',
                spell_id='find-familiar',
                activity_id=activity_id,
                target_id='pc1',
                form_id=form,
                **extra,
            ),
        )
    )
    return O.drain_pending_events(handle)


def _caster(handle):
    live = O._get_live(handle)
    return live, next(c for c in live.initiative if c.entity_id == 'pc1')


def test_a_legal_form_outside_the_list_asks_the_dm_and_spends_nothing() -> None:
    handle, activity_id = _fight()
    before, caster_before = _caster(handle)
    events = _cast(handle, activity_id, 'badger')
    asked = [event for event in events if event.type == 'adjudication_requested']
    assert len(asked) == 1, [event.type for event in events]
    assert asked[0].input_key == ANSWER_KEY
    assert asked[0].source_id == 'find-familiar'
    assert 'badger' in asked[0].question
    # Nothing is spent while the question is open: the Action is still there,
    # and so is every spell slot the caster started with (level 1: two).
    after, caster = _caster(handle)
    assert caster.hp_current == caster_before.hp_current
    assert caster.action_available == caster_before.action_available is True
    assert caster.bonus_action_available == caster_before.bonus_action_available
    assert len(after.initiative) == len(before.initiative)
    # The slot pool lives on the combat, not on the combatant.
    assert before.spell_slots_by_entity['pc1'] == {1: 2}
    assert after.spell_slots_by_entity['pc1'] == {1: 2}


def test_the_dm_answer_seats_the_form_and_spends_the_cast() -> None:
    handle, activity_id = _fight()
    _cast(handle, activity_id, 'badger')
    events = _cast(handle, activity_id, 'badger', adjudications={ANSWER_KEY: True})
    joined = [event for event in events if event.type == 'combatant_joined']
    assert len(joined) == 1, [event.type for event in events]
    live, caster = _caster(handle)
    seated = next(c for c in live.initiative if c.entity_id == joined[0].entity_id)
    # The name the table chose; the numbers the CR 0 Beast stat block gives.
    assert seated.name == 'Badger'
    assert seated.creature_type == 'beast'
    assert seated.hp_current == 1
    assert caster.action_available is False
    assert live.spell_slots_by_entity['pc1'] == {1: 1}


def test_a_form_the_book_forbids_is_refused_without_asking() -> None:
    """"Imp" is a Fiend, not a Beast of Challenge Rating 0. There is nothing
    for the DM to settle, so a round trip would only repeat the refusal.
    """
    handle, activity_id = _fight()
    events = _cast(handle, activity_id, 'imp')
    kinds = [event.type for event in events]
    assert 'adjudication_requested' not in kinds
    assert 'combatant_joined' not in kinds
    assert [event.reason for event in events if event.type == 'cast_failed'] == [
        'target_invalid'
    ]


def test_a_printed_form_still_needs_no_answer() -> None:
    handle, activity_id = _fight()
    events = _cast(handle, activity_id, 'owl')
    kinds = [event.type for event in events]
    assert 'adjudication_requested' not in kinds
    assert 'combatant_joined' in kinds