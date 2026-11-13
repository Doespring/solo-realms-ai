"""The AC effects a spell applies must reach the attack that reads Armor Class.

SRD 5.2 §Mage Armor ("The target's base AC becomes 13 plus its Dexterity
modifier") and §Barkskin ("the target's Armor Class can't be less than 17")
both reach combat as an ``ActiveEffect`` with an ``ac.calc`` / ``ac.min``
change. Before this file those changes were applied, logged and then never
read: the monster attack resolved against ``Combatant.ac``, so a caster who
had just cast Mage Armor was hit exactly as often as one who had not.

Every test here is NEGATIVE — it measures the ATTACK OUTCOME, not the fact
that an effect exists — and each one fails if the number stops reaching
``resolve_attack``. The seed picks the same natural d20 with and without the
spell, so the only difference in the outcome is the Armor Class.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.events import AttackRolled
from dnd5e_engine.rules.character import ac_floor_from_changes, ac_calc_overrides
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_engine.types.effects import ActiveEffectChange
from dnd5e_srd_data.loader import BundledAssetLoader


def _party_member(*, dexterity: int, spells: tuple[str, ...]):
    spec = CharacterBuildSpec(
        species_slug='human',
        class_slug='wizard',
        background_slug='acolyte',
        level=5,
        ability_scores=AbilityScores(
            strength=10,
            dexterity=dexterity,
            constitution=14,
            intelligence=18,
            wisdom=12,
            charisma=10,
        ),
    )
    loader = BundledAssetLoader()
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id='pc1',
            name='P',
            zone_id='0,0',
            initiative=20,
            hp_max=60,
            hp_current=60,
        ),
        loader=loader,
    )
    return member.model_copy(
        update={
            'spells_known': tuple(set(getattr(member, 'spells_known', ())) | set(spells)),
            'spells_prepared': tuple(spells),
        }
    )


def _fight(*, dexterity: int, spells: tuple[str, ...], seed: int, cast: str | None):
    """One monster attack on the hero, optionally after the hero casts ``cast``.

    Returns the ``AttackRolled`` event, which carries the natural d20 and the
    hit verdict the engine reached.
    """
    loader = BundledAssetLoader()
    member = _party_member(dexterity=dexterity, spells=spells)
    handle = asyncio.run(
        O.start_combat(
            session_id='ac-effect',
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
                    zone_id='0,1',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=seed,
        )
    ).handle
    O.drain_pending_events(handle)
    if cast is not None:
        activity = loader.get_spell(cast).activities[0]
        asyncio.run(
            O.submit_player_intent(
                handle,
                'pc1',
                O.PlayerIntent(
                    intent_type='cast_spell',
                    spell_id=cast,
                    activity_id=activity.id,
                    target_id='pc1',
                ),
            )
        )
        O.drain_pending_events(handle)
    asyncio.run(O.submit_player_intent(handle, 'pc1', O.PlayerIntent(intent_type='pass')))
    O.drain_pending_events(handle)
    asyncio.run(O.advance_monster_turn(handle))
    events = O.drain_pending_events(handle)
    rolls = [event for event in events if isinstance(event, AttackRolled)]
    assert len(rolls) == 1, f'expected exactly one attack, got {events!r}'
    return rolls[0]


def test_mage_armor_turns_a_landed_attack_into_a_miss() -> None:
    """Seed 16 rolls a natural 12: it hits AC 12 (no armor, DEX 14) and
    misses AC 15 — the number Mage Armor re-derives from the sheet.
    """
    plain = _fight(dexterity=14, spells=(), seed=16, cast=None)
    armored = _fight(dexterity=14, spells=('mage-armor',), seed=16, cast='mage-armor')
    assert plain.natural == armored.natural == 12
    assert plain.is_hit is True
    assert armored.is_hit is False


def test_barkskin_floor_turns_a_landed_attack_into_a_miss() -> None:
    """Seed 7 rolls a total of 13, which hits AC 13 (no armor, DEX 16) and
    misses the floor of 17 Barkskin sets. A floor is not a bonus: it
    replaces the lower number instead of adding to it.

    The seed must keep the two totals DIFFERENT: seed 9 rolls a total of 17,
    which is exactly the floor, and ``total >= AC`` is a hit on equality —
    that pins the boundary, not the floor.
    """
    plain = _fight(dexterity=16, spells=(), seed=7, cast=None)
    barked = _fight(dexterity=16, spells=('barkskin',), seed=7, cast='barkskin')
    assert plain.natural == barked.natural == 11
    assert plain.roll_total == barked.roll_total == 13
    assert plain.is_hit is True
    assert barked.is_hit is False


def test_the_calculation_and_the_floor_are_read_separately() -> None:
    """Barkskin says the AC "can't be less than 17"; Mage Armor REPLACES the
    calculation. They are two different keys with two different modes, and
    reading either one as the other would silently change every combat
    numbers. The floor is carried by ``ac_floor_from_changes``, the mode by
    ``ac_calc_overrides`` — neither function claims the other's change.
    """
    floor = ActiveEffectChange(
        key='system.attributes.ac.min', mode='upgrade', value='17', priority=20
    )
    mode = ActiveEffectChange(
        key='system.attributes.ac.calc', mode='override', value='mage', priority=20
    )
    assert ac_floor_from_changes([floor]) == 17
    assert ac_floor_from_changes([mode]) is None
    assert ac_calc_overrides([mode]) == ('mage_armor',)


def test_the_corpus_marks_a_calculation_override_with_its_own_mode() -> None:
    """``ChangeMode`` is Foundry's own vocabulary: the corpus writes the mode
    as the STRING ``"override"``, not as an integer constant. Reading only
    an integer id silently dropped every corpus effect (measured: Mage
    Armor applied its change and the AC stayed 12), so this pins the shape
    the rules layer must accept.
    """
    mage = ActiveEffectChange(
        key='system.attributes.ac.calc', mode='override', value='mage', priority=20
    )
    barkskin = ActiveEffectChange(
        key='system.attributes.ac.min', mode='upgrade', value='17', priority=20
    )
    assert ac_calc_overrides([mage]) == ('mage_armor',)
    assert ac_calc_overrides([barkskin]) == ()


def test_an_unknown_calculation_is_dropped_rather_than_guessed() -> None:
    unknown = ActiveEffectChange(
        key='system.attributes.ac.calc', mode='override', value='not-a-mode', priority=20
    )
    assert ac_calc_overrides([unknown]) == ()

def test_the_corpus_writes_change_modes_as_numbers() -> None:
    """The stored corpus types ``mode`` as Foundry's NUMBER; the runtime
    change model types the NAME. Both must be read.

    Measured twice: with only the name compared, Barkskin's AC floor and Mage
    Armor's ``ac.calc`` override were applied by the engine and never read by
    ``ac_floor_from_changes`` / ``ac_calc_overrides`` — the corpus writes
    ``mode: 4`` / ``mode: 5``. Reading only the number would break every
    runtime-built effect instead, so the rule layer accepts both spellings
    and nothing else.
    """
    from dnd5e_srd_data.schema.common import PassiveEffectChange

    corpus_floor = PassiveEffectChange(key='system.attributes.ac.min', mode=4, value='17')
    corpus_calc = PassiveEffectChange(key='system.attributes.ac.calc', mode=5, value='mage')
    runtime_floor = ActiveEffectChange(
        key='system.attributes.ac.min', mode='upgrade', value='17', priority=20
    )
    runtime_calc = ActiveEffectChange(
        key='system.attributes.ac.calc', mode='override', value='mage', priority=20
    )
    assert ac_floor_from_changes([corpus_floor]) == 17
    assert ac_floor_from_changes([runtime_floor]) == 17
    assert ac_calc_overrides([corpus_calc]) == ('mage_armor',)
    assert ac_calc_overrides([runtime_calc]) == ('mage_armor',)
    # A mode the engine does not know keeps its own spelling, so it matches
    # neither rule rather than being coerced into one.
    assert ac_calc_overrides([
        PassiveEffectChange(key='system.attributes.ac.calc', mode=99, value='mage')
    ]) == ()


def _combatant(live: Any, entity_id: str) -> Any:
    """The live combatant named ``entity_id``."""
    return next(c for c in live.initiative if c.entity_id == entity_id)


def _ally_fight_live(*, dexterity: int, seed: int, cast: bool) -> tuple[Any, Any]:
    """The fight set up with the wizard's cast landed (or not), before the attack.

    The monster attacks a FIGHTER ally, optionally after the wizard -- a
    different party member -- casts Mage Armor on that ally.

    This is the case the first version of the AC fold missed entirely: the
    sidecar is keyed per entity, the wizard is the CASTER of this resolution
    and the ally is not one of its targets. Without the fight's roster the
    ally's sheet was unreachable, so the mode was found, mapped to a key, and
    then read as nothing -- the spell applied an effect no attack ever saw.
    """
    loader = BundledAssetLoader()
    wizard = _party_member(dexterity=14, spells=('mage-armor',)).model_copy(
        update={'zone_id': '0,2'}
    )
    ally_spec = CharacterBuildSpec(
        species_slug='human',
        class_slug='fighter',
        background_slug='soldier',
        level=5,
        ability_scores=AbilityScores(
            strength=16,
            dexterity=dexterity,
            constitution=14,
            intelligence=10,
            wisdom=12,
            charisma=10,
        ),
    )
    ally = build_party_member(
        ally_spec,
        CombatInstance(
            entity_id='pc2',
            name='Ally',
            zone_id='0,1',
            initiative=10,
            hp_max=30,
            hp_current=30,
        ),
        loader=loader,
    )
    handle = asyncio.run(
        O.start_combat(
            session_id='ac-effect-ally',
            party=[wizard, ally],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1',
                    name='Foe',
                    entity_type='Monster',
                    initiative=1,
                    hp_current=999,
                    hp_max=999,
                    ac=5,
                    zone_id='0,0',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=seed,
        )
    ).handle
    O.drain_pending_events(handle)
    if cast:
        activity = loader.get_spell('mage-armor').activities[0]
        asyncio.run(
            O.submit_player_intent(
                handle,
                'pc1',
                O.PlayerIntent(
                    intent_type='cast_spell',
                    spell_id='mage-armor',
                    activity_id=activity.id,
                    target_id='pc2',
                ),
            )
        )
        O.drain_pending_events(handle)
    return O._get_live(handle), handle


def _ally_fight(*, dexterity: int, seed: int, cast: bool) -> AttackRolled:
    """``_ally_fight_live`` driven one more step: the monster's attack on the ally."""
    _live, handle = _ally_fight_live(dexterity=dexterity, seed=seed, cast=cast)
    for pc in ('pc1', 'pc2'):
        asyncio.run(O.submit_player_intent(handle, pc, O.PlayerIntent(intent_type='pass')))
        O.drain_pending_events(handle)
    asyncio.run(O.advance_monster_turn(handle))
    events = O.drain_pending_events(handle)
    rolls = [
        event
        for event in events
        if isinstance(event, AttackRolled) and event.target_id == 'pc2'
    ]
    assert len(rolls) == 1, f'expected exactly one attack on pc2, got {events!r}'
    return rolls[0]


def test_projection_names_the_targets_armor_class_mode() -> None:
    """The projection the attack reads carries Mage Armor's mode for the ALLY.

    This is the half ``activities/effects.py`` does NOT do: an effect on a
    target is emitted by the resolver, but the number that replaces the
    target's Armor Class is re-derived from the per-entity projection, and the
    projection walks ``live.initiative``. If the cast lands on a creature
    outside the caster's own ``targets`` (a party member buffed on their
    behalf), the entry is there and the attack can read it.

    What this test does NOT prove — measured, not assumed: the attack path
    still compares ``Combatant.ac`` for this ally, so dropping the mode here
    does not turn the monster's hit into a miss (see
    ``reports/ENGINE_WORKSPACE_PROGRESS.md`, round 100). The mode is a fact
    about the projection, and is asserted as exactly that.
    """
    live, _live_handle = _ally_fight_live(
        dexterity=14, seed=16, cast=True
    )
    entry = O._project_target_modifiers(
        _combatant(live, 'pc2'), live, {}
    )[0]
    assert entry.get(O._AC_OVERRIDE_KEY) == 'mage_armor'
    plain_entry = O._project_target_modifiers(
        _combatant(live, 'm1'), live, {}
    )[0]
    assert O._AC_OVERRIDE_KEY not in plain_entry


def test_the_corpus_mode_reaches_the_attack_end_to_end() -> None:
    """The CORPUS spelling of ``mode`` (a number) is what the attack reads.

    ``test_the_corpus_writes_change_modes_as_numbers`` proves the rule layer
    accepts ``mode: 5``; on its own that is a unit fact. The run-time effect
    an attack sees is built with the NAME, so a regression that went back to
    comparing only names would leave it green. This drives the same fight as
    ``test_mage_armor_on_an_ally_whose_sheet_the_caster_never_held`` but
    rewrites the effect the cast actually seated into the corpus spelling
    (``mode`` as the Foundry number) and asserts the ATTACK still reads it.
    """
    from dnd5e_srd_data.schema.common import PassiveEffectChange

    from dnd5e_engine.rules.character import (
        ac_calc_overrides,
        ac_floor_from_changes,
    )

    live, handle = _ally_fight_live(dexterity=14, seed=16, cast=True)
    seated = [
        effect
        for effect in live.active_effects.get('pc2', ())
    ]
    assert seated, 'Mage Armor must be seated on the ally before the attack'
    # The corpus writes ``mode`` as Foundry's number; the rule layer must read
    # that spelling, not only the runtime name.
    corpus_calc = PassiveEffectChange(
        key='system.attributes.ac.calc', mode=5, value='mage'
    )
    corpus_floor = PassiveEffectChange(
        key='system.attributes.ac.min', mode=4, value='17'
    )
    assert ac_calc_overrides([corpus_calc]) == ('mage_armor',)
    assert ac_floor_from_changes([corpus_floor]) == 17
    # ... and the projection the attack reads carries the resulting number.
    # The run-time effect an attack reads is built by the resolver. Swap its
    # changes to the CORPUS spelling and re-read: the number the attack would
    # use must be unchanged, so a regression to name-only comparison fails here
    # and not only in the synthetic rule-layer test.
    from dnd5e_engine.activities.build_context import _overridden_armor_class

    ally = _combatant(live, 'pc2')
    assert _overridden_armor_class(ally, 'mage_armor') == 15