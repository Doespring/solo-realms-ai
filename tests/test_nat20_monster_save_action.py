"""A monster's own save action resolves, and says why when it cannot (§5, §7).

The Air Elemental's Whirlwind is a Strength-save action: "each Medium or
smaller creature in the monster's space". Before this file a host driving the
monster through ``advance_monster_turn`` got

    KeyError: 'charisma'

because the resolution context was built without the monster's stat-block
numbers and fell back to a spellcasting ability the stat block does not have.
Making the command resolvable also needed the stat-block gate to accept a save
action that makes no attack roll: it refused one as ``action_unavailable``.

The stat-block numbers now come from the monster's own template — the same
scores and Proficiency Bonus its attacks roll at — so its save DC is the one
written in its action and ``@mod`` reads the same scores.
"""

from __future__ import annotations

import asyncio

import dnd5e_engine.orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader


def _air_elemental_fight():
    loader = BundledAssetLoader()
    hero = build_party_member(
        CharacterBuildSpec(
            species_slug='human',
            class_slug='fighter',
            background_slug='soldier',
            level=5,
            ability_scores=AbilityScores(
                strength=16,
                dexterity=14,
                constitution=16,
                intelligence=16,
                wisdom=14,
                charisma=14,
            ),
        ),
        CombatInstance(
            entity_id='pc1',
            name='Fighter',
            zone_id='0,0',
            initiative=20,
            hp_max=44,
            hp_current=44,
        ),
        loader=loader,
    )
    monster = loader.get_monster('air-elemental')
    handle = asyncio.run(
        O.start_combat(
            session_id='whirlwind',
            party=[hero],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1',
                    name=monster.name,
                    entity_type='Monster',
                    initiative=30,
                    hp_current=monster.hp,
                    hp_max=monster.hp,
                    ac=monster.ac,
                    zone_id='1,0',
                    monster_template_slug='air-elemental',
                )
            ],
            grid_scene=GridScene(width=8, height=6),
            rng_seed=7,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _whirlwind(handle):
    asyncio.run(
        O.submit_player_intent(
            handle,
            'm1',
            O.PlayerIntent(
                intent_type='attack',
                target_id='pc1',
                stat_block_action_id='whirlwind',
            ),
        )
    )
    return O.drain_pending_events(handle)


def test_a_monsters_save_action_rolls_its_own_dc() -> None:
    events = _whirlwind(_air_elemental_fight())
    saves = [e for e in events if e.type == 'save_rolled']
    assert saves, [e.type for e in events]
    assert saves[0].dc == 13, saves[0].dc  # written in the action
    assert saves[0].ability == 'str'


def test_a_failed_save_against_it_deals_its_damage() -> None:
    events = _whirlwind(_air_elemental_fight())
    save = next(e for e in events if e.type == 'save_rolled')
    damage = [e for e in events if e.type == 'damage_applied']
    assert save.succeeded is False
    assert damage and damage[0].damage_type == 'thunder', [e.type for e in events]
    # 4d10 + the elemental's CON modifier: never the bare 4d10 without ``@mod``.
    assert damage[0].amount >= 5


def test_a_multiattack_command_is_still_refused_by_name() -> None:
    """Each swing of a Multiattack is its own intent; naming it stays a refusal."""
    handle = _air_elemental_fight()
    asyncio.run(
        O.submit_player_intent(
            handle,
            'm1',
            O.PlayerIntent(
                intent_type='attack',
                target_id='pc1',
                stat_block_action_id='multiattack',
            ),
        )
    )
    events = O.drain_pending_events(handle)
    assert [e.reason for e in events if e.type == 'attack_failed'] == ['action_unavailable']


def test_advance_monster_turn_no_longer_raises() -> None:
    """The host drives monsters through this entrypoint (§7: no exception)."""
    handle = _air_elemental_fight()
    asyncio.run(O.advance_monster_turn(handle))
    kinds = [e.type for e in O.drain_pending_events(handle)]
    assert 'attack_rolled' in kinds or 'save_rolled' in kinds, kinds