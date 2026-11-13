"""The command a summoner gives its summon (SRD 5.2 Summon Dragon).

"It obeys your verbal commands (no action required by you)." A command names
a stat-block action; the summon spends its own turn carrying it out. §5: a
command the standard interfaces accept must actually change the world, and a
command they refuse must say so — never a silent no-op, never a second
resolution of the same command (§9).
"""

from __future__ import annotations

import asyncio

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

L = BundledAssetLoader()


def _draconic_spirit_fight():
    spec = CharacterBuildSpec(
        species_slug='human',
        class_slug='wizard',
        background_slug='acolyte',
        level=7,
        ability_scores=AbilityScores(
            strength=8,
            dexterity=14,
            constitution=14,
            intelligence=18,
            wisdom=10,
            charisma=10,
        ),
    )
    pc = build_party_member(
        spec,
        CombatInstance(
            entity_id='pc1',
            name='P',
            zone_id='0,0',
            initiative=20,
            hp_max=44,
            hp_current=44,
        ),
        loader=L,
    )
    pc = pc.model_copy(
        update={
            'spells_known': ('summon-dragon',),
            'spells_prepared': ('summon-dragon',),
        }
    )
    handle = asyncio.run(
        O.start_combat(
            session_id='sc1',
            party=[pc],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1',
                    name='T',
                    entity_type='Monster',
                    initiative=10,
                    hp_current=99,
                    hp_max=99,
                    ac=12,
                    zone_id='0,1',
                )
            ],
            grid_scene=GridScene(width=16, height=12),
            rng_seed=1,
        )
    ).handle
    O.drain_pending_events(handle)
    activity = L.get_spell('summon-dragon').activities[0]
    asyncio.run(
        O.submit_player_intent(
            handle,
            'pc1',
            O.PlayerIntent(
                intent_type='cast_spell',
                spell_id='summon-dragon',
                activity_id=activity.id,
                slot_level=4,
                target_zone_id='1,1',
            ),
        )
    )
    O.drain_pending_events(handle)
    live = O._get_live(handle)
    summon = next(iter(live.summons.values()))
    live.actor_zone[summon.entity_id] = '1,1'
    live.actor_zone['m1'] = '0,1'
    live.current_turn_index = next(
        i for i, c in enumerate(live.initiative) if c.entity_id == summon.entity_id
    )
    return handle, live, summon.entity_id


def test_a_command_is_recorded_and_resolved_on_the_summons_turn() -> None:
    handle, live, summon_id = _draconic_spirit_fight()
    target = next(c for c in live.initiative if c.entity_id == 'm1')
    before = target.hp_current
    asyncio.run(
        O.submit_player_intent(
            handle,
            summon_id,
            O.PlayerIntent(intent_type='attack', stat_block_action_id='multiattack'),
        )
    )
    O.drain_pending_events(handle)
    assert live.summon_commands.get(summon_id) == 'multiattack'
    asyncio.run(O.advance_monster_turn(handle))
    events = O.drain_pending_events(handle)
    O.drain_pending_events(handle)
    types = [e.type for e in events]
    # The Multiattack is its listed parts: Rend and the Breath Weapon.
    assert types.count('attack_rolled') >= 1, types
    assert 'damage_applied' in types, types
    damage = [e for e in events if e.type == 'damage_applied']
    assert damage[0].target_id == 'm1'
    fresh = next(c for c in O._get_live(handle).initiative if c.entity_id == 'm1')
    assert fresh.hp_current < before, (before, fresh.hp_current, [e.type for e in events])
    assert summon_id not in live.summon_commands


def test_the_command_is_spent_once() -> None:
    """§9: replaying the same command must not grant a second action."""
    handle, live, summon_id = _draconic_spirit_fight()
    asyncio.run(
        O.submit_player_intent(
            handle,
            summon_id,
            O.PlayerIntent(intent_type='attack', stat_block_action_id='multiattack'),
        )
    )
    O.drain_pending_events(handle)
    asyncio.run(O.advance_monster_turn(handle))
    O.drain_pending_events(handle)
    asyncio.run(O.advance_monster_turn(handle))
    second = O.drain_pending_events(handle)
    # The second turn is a different creature's; nothing re-ran the summon.
    # The second turn is a different creature's; nothing re-ran the summon.
    assert not [
        e
        for e in second
        if e.type in ('attack_rolled', 'damage_applied')
        and getattr(e, 'actor_id', None) == summon_id
    ], [e.type for e in second]
