"""An attack that cannot resolve says why (§5), it does not vanish (§7).

The `attack` intent carries the weapon the hero swings. Two shapes used to
produce only ``intent_submitted`` and silence: a weapon slug the loader does
not know (a typo, an item outside the release) and no slug at all. Silence is
indistinguishable from "the rules produced nothing", and neither shape is a
DM decision — the engine simply does not guess a weapon (§8).

Both are now refusals with their own reason, before anything is spent.
"""

from __future__ import annotations

import asyncio

import dnd5e_engine.orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader


def _fight():
    spec = CharacterBuildSpec(
        species_slug='human',
        class_slug='fighter',
        background_slug='soldier',
        level=5,
        ability_scores=AbilityScores(
            strength=16,
            dexterity=14,
            constitution=16,
            intelligence=10,
            wisdom=12,
            charisma=10,
        ),
    )
    fighter = build_party_member(
        spec,
        CombatInstance(
            entity_id='pc1',
            name='Fighter',
            zone_id='0,0',
            initiative=20,
            hp_max=44,
            hp_current=44,
        ),
        loader=BundledAssetLoader(),
    )
    handle = asyncio.run(
        O.start_combat(
            session_id='attack-refusal',
            party=[fighter],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1',
                    name='Dummy',
                    entity_type='Monster',
                    initiative=1,
                    hp_current=200,
                    hp_max=200,
                    ac=12,
                    zone_id='1,0',
                )
            ],
            grid_scene=GridScene(width=8, height=6),
            rng_seed=5,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _attack(handle, **kwargs):
    asyncio.run(
        O.submit_player_intent(
            handle,
            'pc1',
            O.PlayerIntent(intent_type='attack', target_id='m1', **kwargs),
        )
    )
    return O.drain_pending_events(handle)


def test_an_attack_without_a_weapon_is_refused_by_name() -> None:
    events = _attack(_fight())
    reasons = [e.reason for e in events if e.type == 'attack_failed']
    assert reasons == ['weapon_required'], [e.type for e in events]


def test_an_unknown_weapon_is_refused_by_name() -> None:
    events = _attack(_fight(), weapon_id='flaming-sword-of-nowhere')
    reasons = [e.reason for e in events if e.type == 'attack_failed']
    assert reasons == ['unknown_weapon'], [e.type for e in events]


def test_a_refused_attack_spends_nothing() -> None:
    handle = _fight()
    initial = next(c for c in O._get_live(handle).initiative if c.entity_id == 'pc1')
    _attack(handle)
    after = next(c for c in O._get_live(handle).initiative if c.entity_id == 'pc1')
    assert after.action_available == initial.action_available is True


def test_the_named_weapon_still_attacks() -> None:
    events = _attack(_fight(), weapon_id='longsword')
    assert 'attack_rolled' in [e.type for e in events]
    assert 'attack_failed' not in [e.type for e in events]


def test_the_refusal_reason_has_russian_text() -> None:
    from dnd5e_engine import labels

    for reason in ('unknown_weapon', 'weapon_required'):
        text = labels.label(reason) if hasattr(labels, 'label') else None
        assert text and text != reason, reason