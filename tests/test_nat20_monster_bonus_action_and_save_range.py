"""A monster's save action reaches only its own range, and its Bonus Action is
a Bonus Action (§5, §7).

Two measured defects (r204):

1. A commanded save action that places a template — a dragon's 30-foot cone of
   fire, a fear aura's radius — resolved against the NAMED target wherever it
   stood. ``_stat_block_target_out_of_range`` read only
   ``_monster_attack_range_ft``, which returns ``None`` for a self-centred
   area, so the gate was skipped and a hero 60 feet away took the breath. The
   monster's own turn already aimed the area (``_resolve_monster_parts``); the
   command path did not.
2. A stat-block action whose typed ``activation.type`` is ``"bonus"`` (the
   pirate captain's Captain's Charm, the elephant's Trample) was priced as the
   monster's ACTION: it spent ``action_available``, left the Bonus Action
   unspent, and thereby blocked the attack the monster still had. A second
   bonus command was then refused for the wrong reason (no Action), and a
   monster could never do both a Bonus Action and an Action on one turn.

The fix prices the command from the action's own ``activation.type``, spends
the Bonus Action it names, and gates a templated save action by the geometry
of its own area from the monster's cell.
"""

from __future__ import annotations

import asyncio

import dnd5e_engine.orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

HERO_ABILITIES = AbilityScores(
    strength=16,
    dexterity=14,
    constitution=16,
    intelligence=16,
    wisdom=14,
    charisma=14,
)


def _fight(monster_slug: str, hero_zone: str, width: int = 30, height: int = 20):
    loader = BundledAssetLoader()
    hero = build_party_member(
        CharacterBuildSpec(
            species_slug='human',
            class_slug='fighter',
            background_slug='soldier',
            level=5,
            ability_scores=HERO_ABILITIES,
        ),
        CombatInstance(
            entity_id='pc1',
            name='Fighter',
            zone_id=hero_zone,
            initiative=20,
            hp_max=44,
            hp_current=44,
        ),
        loader=loader,
    )
    monster = loader.get_monster(monster_slug)
    handle = asyncio.run(
        O.start_combat(
            session_id='r204',
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
                    zone_id='0,0',
                    monster_template_slug=monster_slug,
                )
            ],
            grid_scene=GridScene(width=width, height=height),
            rng_seed=7,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _command(handle, action_id: str, target_id: str = 'pc1', **extra):
    asyncio.run(
        O.submit_player_intent(
            handle,
            'm1',
            O.PlayerIntent(
                intent_type='attack',
                target_id=target_id,
                stat_block_action_id=action_id,
                **extra,
            ),
        )
    )
    return O.drain_pending_events(handle)


def _monster(handle):
    return next(c for c in O._get_live(handle).initiative if c.entity_id == 'm1')


def test_a_breath_weapon_reaches_only_its_own_cone() -> None:
    """A 30-foot cone: 20 ft away it resolves as an area, 60 ft it is refused.

    The command names the same target both times; only the distance changes.
    """
    # 20 ft: inside the cone — the area is placed, the save is rolled.
    events = _command(_fight('young-gold-dragon', '0,4'), 'fire-breath')
    areas = [e for e in events if e.type == 'area_targeted']
    assert areas, [e.type for e in events]
    assert areas[0].shape == 'cone'
    assert areas[0].size_ft == 30
    assert areas[0].affected_ids == ['pc1']
    assert areas[0].source_id == 'fire-breath'
    assert [e for e in events if e.type == 'save_rolled' and e.target_id == 'pc1']
    assert [e for e in events if e.type == 'damage_applied']

    # 60 ft: outside it — a named refusal, and nothing resolves.
    events = _command(_fight('young-gold-dragon', '0,12'), 'fire-breath')
    failures = [e for e in events if e.type == 'attack_failed']
    assert [(e.reason, e.target_id) for e in failures] == [('out_of_range', 'pc1')]
    assert not [e for e in events if e.type in ('save_rolled', 'damage_applied', 'area_targeted')]

    # The aim the resolution would take is the aim the gate measures: a cone
    # turned away from the named target is refused, not spent on nobody.
    events = _command(_fight('young-gold-dragon', '0,4'), 'fire-breath', direction=(0, -1))
    assert [e.reason for e in events if e.type == 'attack_failed'] == ['out_of_range']
    assert not [e for e in events if e.type in ('save_rolled', 'damage_applied', 'area_targeted')]


def test_a_counted_or_placed_area_keeps_its_printed_range() -> None:
    """A template's own feet are a placement range, not decoration.

    Two shapes the cone test cannot see: a ``creature_count == 1`` line (the
    Chain Devil's Conjure Infernal Chain, a 60-ft reach that pulls 30 ft along
    its line) and a ``target``-anchored cylinder (the Storm Giant's Lightning
    Storm, 10-ft cylinder at 500 ft). Both used to resolve against a named
    target anywhere on the map: ``_monster_area`` skips a one-creature area and
    ``_monster_attack_range_ft`` returns ``None`` for every template.

    A counted area's own template is where the effect happens, NOT where the
    victim must stand: 45 ft is inside the chain's printed 60 ft even though it
    is past the 30-ft line (measured by the review: the gate was stricter than
    the resolution here).
    """
    # Inside the printed range the one-creature line resolves — 20 ft and the
    # 45 ft that lies past the line but inside the reach.
    events = _command(_fight('chain-devil', '0,4'), 'conjure-infernal-chain')
    assert [e for e in events if e.type == 'save_rolled'], [e.type for e in events]
    events = _command(_fight('chain-devil', '0,9'), 'conjure-infernal-chain')
    assert [e for e in events if e.type == 'save_rolled'], [e.type for e in events]
    # Twice the range (100 ft > 60 ft) is refused.
    events = _command(_fight('chain-devil', '0,20', height=40), 'conjure-infernal-chain')
    assert [e.reason for e in events if e.type == 'attack_failed'] == ['out_of_range']
    assert not [e for e in events if e.type == 'save_rolled']

    # The cylinder lands on the named target within 500 ft, never at 600 ft.
    events = _command(_fight('storm-giant', '0,20', height=40), 'lightning-storm')
    areas = [e for e in events if e.type == 'area_targeted']
    assert areas and areas[0].shape == 'cylinder', [e.type for e in events]
    assert [e for e in events if e.type == 'save_rolled']
    events = _command(_fight('storm-giant', '0,120', height=140), 'lightning-storm')
    assert [e.reason for e in events if e.type == 'attack_failed'] == ['out_of_range']
    assert not [e for e in events if e.type == 'save_rolled']


def test_a_monster_bonus_action_spends_the_bonus_action_not_the_action() -> None:
    """Captain's Charm is a typed Bonus Action: one per turn, Action untouched."""
    handle = _fight('pirate-captain', '0,1')

    _command(handle, 'captains-charm')
    after_charm = _monster(handle)
    assert after_charm.bonus_action_available is False
    assert after_charm.action_available is True

    # The SECOND bonus command is refused on the Bonus Action — readable reason.
    events = _command(handle, 'captains-charm')
    assert [e.reason for e in events if e.type == 'attack_failed'] == ['no_action_economy']
    assert _monster(handle).action_available is True

    # The Action the monster still had is real: the rapier swings.
    events = _command(handle, 'rapier')
    assert [e for e in events if e.type == 'attack_rolled'], [e.type for e in events]
    assert _monster(handle).action_available is False

    # And a THIRD bonus this turn is still refused (`no_action_economy`).
    events = _command(handle, 'captains-charm')
    assert [e.reason for e in events if e.type == 'attack_failed'] == ['no_action_economy']

    # A Bonus Action printed next to a non-cost activity (the Basilisk's
    # Petrifying Gaze carries a ``turnEnd`` repeat save) is still a Bonus
    # Action: the gaze does not eat the Action, and the bite still swings.
    handle = _fight('basilisk', '0,1')
    _command(handle, 'petrifying-gaze')
    assert _monster(handle).bonus_action_available is False
    assert _monster(handle).action_available is True
    events = _command(handle, 'bite')
    assert [e for e in events if e.type in ('attack_rolled', 'save_rolled')], [
        e.type for e in events
    ]
    assert _monster(handle).action_available is False
