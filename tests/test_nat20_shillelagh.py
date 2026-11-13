"""SRD 5.2 Shillelagh rewrites the club or quarterstaff it enchants.

"the weapon's damage die becomes a d8" and "you can use your spellcasting
ability instead of Strength for the attack and damage rolls of melee
attacks using that weapon". Before this round the engine applied neither:
``EffectApplied`` was emitted and the weapon stayed a plain d6 swung with
Strength, so the spell looked implemented while changing nothing (§7).

Every check drives the public ``submit_player_intent`` path and reads the
resulting events and live state, never a handler's return value.
"""

from __future__ import annotations

import asyncio

from dnd5e_engine import orchestrator
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

ENCHANT_ACTIVITY = "B4nxtIzrCEGEzq1T"


def _caster(strength: int, wisdom: int):
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="druid",
        background_slug="acolyte",
        level=5,
        ability_scores=AbilityScores(
            strength=strength,
            dexterity=10,
            constitution=14,
            intelligence=10,
            wisdom=wisdom,
            charisma=10,
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


def _fight(seed: int = 1, *, strength: int = 8, wisdom: int = 18):
    handle = asyncio.run(
        orchestrator.start_combat(
            session_id="shillelagh",
            party=[_caster(strength, wisdom)],
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


def _cast(handle) -> list:
    asyncio.run(
        orchestrator.submit_player_intent(
            handle,
            "pc1",
            orchestrator.PlayerIntent(
                intent_type="cast_spell",
                spell_id="shillelagh",
                weapon_id="club",
                activity_id=ENCHANT_ACTIVITY,
            ),
        )
    )
    return orchestrator.drain_pending_events(handle)


def _attack(handle) -> list:
    asyncio.run(
        orchestrator.submit_player_intent(
            handle,
            "pc1",
            orchestrator.PlayerIntent(
                intent_type="attack",
                weapon_id="club",
                target_id="m1",
            ),
        )
    )
    return orchestrator.drain_pending_events(handle)


def _attack_modifier(events) -> int:
    rolled = [e for e in events if e.type == "attack_rolled"]
    assert rolled, [e.type for e in events]
    return rolled[0].modifier


def _damage(events) -> list:
    return [e.amount for e in events if e.type == "damage_applied"]


def _plain_hit_damage(events) -> list:
    """Damage from non-critical hits: a crit doubles the dice and would hide
    the die size behind its maximum."""
    return [
        e.amount
        for e in events
        if e.type == "damage_applied" and not e.is_crit
    ]


def test_the_cast_is_offered_at_all():
    """A cantrip the engine refuses is not covered; this one must resolve."""
    events = _cast(_fight())
    types = [e.type for e in events]
    assert "cast_failed" not in types, [
        (e.type, getattr(e, "reason", None)) for e in events
    ]
    assert "effect_applied" in types


def test_the_effect_names_the_weapon_it_enchants():
    handle = _fight()
    _cast(handle)
    effects = orchestrator._get_live(handle).active_effects.get("pc1") or []
    assert len(effects) == 1, [e.name for e in effects]
    assert effects[0].flags.get("enchanted_weapon") == "club"


def test_the_damage_die_becomes_a_d8():
    """A club's own die is 1d4; Shillelagh makes it a d8.

    The club is the clean carrier: unlike a quarterstaff it has no Versatile
    d8 to be confused with the enchantment. Critical hits double the dice and
    blur the sizes, so only ordinary hits are compared. With STR 8 (-1)
    replaced by WIS 18 (+4): 1d4 + 4 rolls 5..8, 1d8 + 4 rolls 5..12. The
    ceiling is what separates them.
    """
    rolls = []
    for seed in range(1, 41):
        handle = _fight(seed)
        _cast(handle)
        rolls.extend(_plain_hit_damage(_attack(handle)))
    assert len(rolls) >= 20, rolls
    # 1d4 + 4 tops out at 8; 1d8 + 4 reaches 12. The ceiling is the proof.
    assert min(rolls) >= 5, rolls
    assert max(rolls) >= 9, rolls
    assert max(rolls) <= 12, rolls


def test_the_attack_uses_the_spellcasting_ability_not_strength():
    """STR 8 is -1 and proficiency is +3, so STR gives +2 and WIS 18 gives +7."""
    assert _attack_modifier(_attack(_fight())) == 2
    handle = _fight()
    _cast(handle)
    assert _attack_modifier(_attack(handle)) == 7


def test_the_better_of_strength_and_spellcasting_is_what_counts():
    """The rider writes max(STR, spellcasting): a strong caster keeps STR."""
    handle = _fight(strength=18, wisdom=10)
    _cast(handle)
    # STR 18 is +4, WIS 10 is +0, proficiency +3: both land 7.
    assert _attack_modifier(_attack(handle)) == 7


def test_damage_carries_the_spellcasting_modifier():
    """1d8 + 4: the lowest die roll still lands 5 damage."""
    lowest = 99
    for seed in range(1, 25):
        handle = _fight(seed)
        _cast(handle)
        for amount in _damage(_attack(handle)):
            lowest = min(lowest, amount)
    assert lowest == 5, lowest


def test_the_enchantment_is_scoped_to_its_weapon():
    """The flag names one weapon, so the lookup must stay scoped to it."""
    handle = _fight()
    _cast(handle)
    live = orchestrator._get_live(handle)
    caster = next(c for c in live.initiative if c.entity_id == "pc1")
    effects = live.active_effects.get("pc1") or ()
    # The enchanted quarterstaff resolves to the spellcasting ability ...
    assert orchestrator._weapon_enchantment_spellcasting(
        live, caster, BundledAssetLoader().get_weapon("club")
    )
    # ... and a weapon the flag does not name does not.
    assert not orchestrator._weapon_enchantment_spellcasting(
        live, caster, BundledAssetLoader().get_weapon("longsword")
    )
    assert effects[0].flags.get("enchanted_weapon") == "club"
