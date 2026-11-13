"""SRD 5.2 True Strike: the attack it names uses the caster's spellcasting
ability and adds the spell's own extra Radiant damage.

"Guided by a flash of magical insight, you make one attack with the weapon
used in the spell's casting. ... if the attack deals damage, it can be
Radiant damage or the weapon's normal damage type ... the attack's extra
damage increases when you reach character levels 5, 11, and 17 (2d6 at 11)."

The spell ships THREE activities: one EnchantActivity carrying two effects,
each naming (riders.activity) the attack it belongs to, plus the melee and
the ranged attack themselves. Before this round the engine applied BOTH
effects for a single cast, the chosen attack got no ability modifier at all
(attack.ability says "spellcasting", a role, not one of the six scores) and
the rider damage never landed, so the spell looked implemented while
changing nothing (§7).

Every check drives the public submit_player_intent path and reads the
resulting events and live state, never a handler's return value.
"""

from __future__ import annotations

import asyncio

from dnd5e_engine import orchestrator
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

ENCHANT_ACTIVITY = "4TlVwMTDg8tqME2z"
MELEE_ATTACK = "CiLsaHBi9F6eIXUS"
RANGED_ATTACK = "cGNXKjpoRima6wjQ"


def _caster(charisma: int, level: int = 11):
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="bard",
        background_slug="acolyte",
        level=level,
        ability_scores=AbilityScores(
            strength=8,
            dexterity=10,
            constitution=14,
            intelligence=10,
            wisdom=10,
            charisma=charisma,
        ),
    )
    return build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1",
            name="Caster",
            zone_id="0,0",
            initiative=20,
            hp_max=40,
            hp_current=40,
        ),
        loader=BundledAssetLoader(),
    )


def _fight(seed: int = 1, *, charisma: int = 18, level: int = 11):
    handle = asyncio.run(
        orchestrator.start_combat(
            session_id="true-strike",
            party=[_caster(charisma, level)],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1",
                    name="Target",
                    entity_type="Monster",
                    initiative=1,
                    hp_current=999,
                    hp_max=999,
                    ac=2,
                    zone_id="1,0",
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=seed,
        )
    ).handle
    orchestrator.drain_pending_events(handle)
    return handle


def _cast(handle, activity_id):
    asyncio.run(
        orchestrator.submit_player_intent(
            handle,
            "pc1",
            orchestrator.PlayerIntent(
                intent_type="cast_spell",
                spell_id="true-strike",
                weapon_id="club",
                activity_id=activity_id,
                target_id="m1",
            ),
        )
    )
    return orchestrator.drain_pending_events(handle)


def _caster_effects(handle) -> list:
    live = orchestrator._get_live(handle)
    return list(live.active_effects.get("pc1", ()))


def _damage(events) -> list:
    return [(e.amount, e.damage_type) for e in events if e.type == "damage_applied"]


def test_named_melee_activity_applies_only_its_own_rider():
    """One cast names one attack, so exactly one of the two riders fires."""
    handle = _fight()
    _cast(handle, MELEE_ATTACK)
    names = [effect.name for effect in _caster_effects(handle)]
    assert names == ["True Strike"], names


def test_named_ranged_activity_applies_only_its_own_rider():
    handle = _fight()
    _cast(handle, RANGED_ATTACK)
    names = [effect.name for effect in _caster_effects(handle)]
    assert names == ["Ranged Weapon"], names


def test_melee_cast_swings_with_the_spellcasting_ability():
    """CH 18 (+4) plus proficiency (+4) at L11 makes the swing d20 + 8.

    ``attack.ability`` is the literal "spellcasting", so before the fix
    ``ability_mod("spellcasting")`` read as 10 (+0) and only proficiency was
    added — a legal-looking Character Sheet with a +4 attack (§5).
    """
    handle = _fight()
    events = _cast(handle, MELEE_ATTACK)
    assert _attack_modifier(events) == 8, _attack_modifier(events)


def test_charisma_drives_that_modifier():
    """CH 12 (+1) makes the same swing d20 + 5, so the +8 is CH-derived."""
    handle = _fight(charisma=12)
    events = _cast(handle, MELEE_ATTACK)
    assert _attack_modifier(events) == 5, _attack_modifier(events)


def test_melee_attack_adds_two_d6_radiant_at_level_11():
    """2d6 at L11 lands as its own typed damage, not folded into the weapon."""
    handle = _fight()
    events = _cast(handle, MELEE_ATTACK)
    radiant = [amount for amount, damage_type in _damage(events) if damage_type == "radiant"]
    assert radiant, _damage(events)
    assert 2 <= radiant[0] <= 12, radiant


def test_ranged_attack_adds_the_same_riders():
    """The ranged rider words the same damage as a TYPED bonus."""
    handle = _fight()
    events = _cast(handle, RANGED_ATTACK)
    radiant = [amount for amount, damage_type in _damage(events) if damage_type == "radiant"]
    assert radiant, _damage(events)
    assert 2 <= radiant[0] <= 12, radiant


def test_no_level_bonus_below_level_five():
    """L4 has no extra damage: the rider resolves to 0d6, not to 1d6."""
    handle = _fight(level=4)
    events = _cast(handle, MELEE_ATTACK)
    radiant = [
        amount
        for amount, damage_type in _damage(events)
        if damage_type == "radiant" and amount > 0
    ]
    assert not radiant, _damage(events)


def test_cast_without_a_chosen_activity_is_refused():
    """Two attack activities, no choice: refuse (§5) instead of firing both."""
    handle = _fight()
    asyncio.run(
        orchestrator.submit_player_intent(
            handle,
            "pc1",
            orchestrator.PlayerIntent(
                intent_type="cast_spell",
                spell_id="true-strike",
                weapon_id="club",
                target_id="m1",
            ),
        )
    )
    events = orchestrator.drain_pending_events(handle)
    assert [event.type for event in events if event.type == "cast_failed"], [
        event.type for event in events
    ]
    assert not _caster_effects(handle), _caster_effects(handle)


def _attack_modifier(events) -> int:
    rolled = [event for event in events if event.type == "attack_rolled"]
    assert rolled, [event.type for event in events]
    return rolled[0].modifier
