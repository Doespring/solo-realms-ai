"""A failed save must change something, or say why it does not (§7).

SRD 5.2 spells state their failure branch in prose. The corpus turns most of
them into machine-readable ``effects`` / ``riders``; a few carry only the save
while their own text names the consequence — Grease and Sleet Storm ("or have
the Prone condition"), Telekinesis ("is Restrained"). Before this file the
engine rolled such a save, emitted ``save_rolled`` with ``succeeded: false``
and changed NOTHING: the player saw a failed save and no effect.

The engine must not invent the condition from prose (§2: no guessing) and must
not report a save whose failure was a no-op (§7). It asks the DM (§8) — once
per cast, naming the condition and the creature, with the roll and the slot
already spent as the book says.
"""

from __future__ import annotations

import asyncio

import dnd5e_engine.orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader


def _cast(spell: str, seed: int):
    loader = BundledAssetLoader()
    spec = CharacterBuildSpec(
        species_slug='human',
        class_slug='wizard',
        background_slug='acolyte',
        level=17,
        ability_scores=AbilityScores(
            strength=16,
            dexterity=14,
            constitution=16,
            intelligence=18,
            wisdom=16,
            charisma=16,
        ),
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id='pc1',
            name='P',
            zone_id='0,0',
            initiative=20,
            hp_max=300,
            hp_current=300,
        ),
        loader=loader,
    ).model_copy(update={'spells_known': (spell,), 'spells_prepared': (spell,)})
    handle = asyncio.run(
        O.start_combat(
            session_id='fail-effect-' + spell,
            party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1',
                    name='Foe',
                    entity_type='Monster',
                    initiative=1,
                    hp_current=999,
                    hp_max=999,
                    ac=5,
                    zone_id='1,0',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=seed,
        )
    ).handle
    O.drain_pending_events(handle)
    activity = loader.get_spell(spell).activities[0]
    asyncio.run(
        O.submit_player_intent(
            handle,
            'pc1',
            O.PlayerIntent(
                intent_type='cast_spell',
                spell_id=spell,
                activity_id=activity.id,
                target_id='m1',
            ),
        )
    )
    return handle, O.drain_pending_events(handle)


def test_a_failed_save_with_no_data_reaches_the_dm() -> None:
    handle, events = _cast('grease', seed=1)
    saves = [e for e in events if e.type == 'save_rolled']
    assert [(e.natural, e.succeeded) for e in saves] == [(5, False)]
    asked = [e for e in events if e.type == 'adjudication_requested']
    assert len(asked) == 1, [e.type for e in events]
    assert asked[0].input_key == 'grease:fail_effect'
    assert asked[0].source_id == 'grease'
    assert 'prone' in asked[0].question
    # The cast stands as the book says: the slot and the Action are spent.
    live = O._get_live(handle)
    assert live.spell_slots_by_entity['pc1'].get(1, 0) < 4


def test_a_successful_save_asks_nothing() -> None:
    """Nothing failed, so there is nothing for the DM to settle."""
    _, events = _cast('hold-monster', seed=6)
    assert [e.type for e in events if e.type == 'adjudication_requested'] == []


def test_a_spell_whose_data_carries_the_effect_asks_nothing() -> None:
    """Hold Monster ships the effect; the engine applies it and stops."""
    _, events = _cast('hold-monster', seed=1)
    kinds = [e.type for e in events]
    assert 'condition_applied' in kinds
    assert 'adjudication_requested' not in kinds


def test_a_damage_spell_asks_nothing() -> None:
    """Fireball's failure branch is damage, which the data carries."""
    _, events = _cast('fireball', seed=1)
    kinds = [e.type for e in events]
    assert 'damage_applied' in kinds
    assert 'adjudication_requested' not in kinds