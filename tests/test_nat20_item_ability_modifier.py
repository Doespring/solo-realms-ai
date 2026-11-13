"""A magic item whose formula asks for an ability modifier does not crash.

Shield of the Cavalier's Shield Bash rolls ``2d6 + 2 + @mod``; Dragon Orb and
Staff of Healing cast a spell whose healing is ``@mod``-based. A 2024 item
block names no ability, and a fighter has no spellcasting ability at all, so
both paths used to raise out of ``submit_player_intent``:

    ValueError: @mod requires a governing ability but none was supplied

An exception is the worst shape of §5 refusal: the player gets no event, no
reason, and the host sees a stack trace. The item's own user supplies the
ability; a spell still takes the item's or the caster's when either names one.
"""

from __future__ import annotations

import asyncio

import dnd5e_engine.orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SHIELD_BASH = ('shield-of-the-cavalier', 'ABPaLpY5mrKpB4ou')
DRAGON_ORB = ('dragon-orb', 'gU19MBO5R6a6xhIX')


def _fight_with(item_slug: str):
    loader = BundledAssetLoader()
    spec = CharacterBuildSpec(
        species_slug='human',
        class_slug='fighter',
        background_slug='soldier',
        level=10,
        ability_scores=AbilityScores(
            strength=16,
            dexterity=14,
            constitution=16,
            intelligence=10,
            wisdom=12,
            charisma=10,
        ),
    )
    hero = build_party_member(
        spec,
        CombatInstance(
            entity_id='pc1',
            name='Fighter',
            zone_id='0,0',
            initiative=20,
            hp_max=80,
            hp_current=40,
        ),
        loader=loader,
    )
    hero = hero.model_copy(
        update={
            'equipment': tuple(set(hero.equipment or ()) | {item_slug}),
            'attuned_items': (item_slug,),
        }
    )
    handle = asyncio.run(
        O.start_combat(
            session_id='item-ability',
            party=[hero],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1',
                    name='Dummy',
                    entity_type='Monster',
                    initiative=1,
                    hp_current=200,
                    hp_max=200,
                    ac=5,
                    zone_id='1,0',
                )
            ],
            grid_scene=GridScene(width=8, height=6),
            rng_seed=5,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle, loader


def _use(handle, item_slug, activity_id):
    asyncio.run(
        O.submit_player_intent(
            handle,
            'pc1',
            O.PlayerIntent(
                intent_type='use_item',
                item_id=item_slug,
                activity_id=activity_id,
                target_id='m1',
            ),
        )
    )
    return O.drain_pending_events(handle)


def test_an_items_attack_that_wants_a_modifier_resolves() -> None:
    slug, activity_id = SHIELD_BASH
    handle, _ = _fight_with(slug)
    events = _use(handle, slug, activity_id)
    kinds = [e.type for e in events]
    assert 'attack_rolled' in kinds, kinds
    damage = [e for e in events if e.type == 'damage_applied']
    assert damage and damage[0].amount >= 3, kinds


def test_an_items_spell_that_wants_a_modifier_resolves() -> None:
    slug, activity_id = DRAGON_ORB
    handle, _ = _fight_with(slug)
    events = _use(handle, slug, activity_id)
    kinds = [e.type for e in events]
    assert 'healing_applied' in kinds, kinds
    healed = next(e for e in events if e.type == 'healing_applied')
    assert healed.amount > 0


def test_a_mundane_swing_keeps_its_weapon_ability() -> None:
    """The fallback is for items only: a longsword swing stays Strength."""
    handle, _ = _fight_with('longsword')
    asyncio.run(
        O.submit_player_intent(
            handle,
            'pc1',
            O.PlayerIntent(intent_type='attack', target_id='m1', weapon_id='longsword'),
        )
    )
    events = O.drain_pending_events(handle)
    rolled = next(e for e in events if e.type == 'attack_rolled')
    # STR 16 -> +3, proficiency +4 at level 10: the modifier is the weapon's.
    assert rolled.modifier == 7, rolled.modifier