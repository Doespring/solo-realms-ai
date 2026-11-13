"""SRD 5.2 choose-the-damage-type: the player picks, the engine applies.

Chromatic Orb and Sorcerous Burst list several damage types and leave the
pick to the caster. Before this seam the engine silently used the first
listed type, which is a decision the book gives the player (section 5).
Every test drives the public submit_player_intent path.
"""
import asyncio

from dnd5e_engine import (
    GridScene,
    PlayerIntent,
    drain_pending_events,
    get_live,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec
from dnd5e_srd_data.loader import BundledAssetLoader

SORCEROUS = "sorcerous-burst"
CHROMATIC = "chromatic-orb"
OFFERED = ("acid", "cold", "fire", "lightning", "poison", "psychic", "thunder")


def _fight(*, seed=1):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="acolyte",
        level=11,
        ability_scores=AbilityScores(
            strength=10, dexterity=14, constitution=14,
            intelligence=18, wisdom=10, charisma=10,
        ),
    )
    party = build_party_member(
        spec,
        CombatInstance(entity_id="pc1", name="P", zone_id="0,0",
                       initiative=20, hp_max=60, hp_current=60),
        loader=BundledAssetLoader(),
    )
    target = EncounterMemberSpec(
        entity_id="m1", name="Target", entity_type="Monster",
        initiative=1, hp_current=999, hp_max=999, ac=2, zone_id="1,0",
    )
    handle = asyncio.run(start_combat(
        session_id="s", party=[party], encounter=[target],
        grid_scene=GridScene(width=10, height=10), rng_seed=seed,
    )).handle
    drain_pending_events(handle)
    return handle


def _cast(handle, slug, **kw):
    asyncio.run(submit_player_intent(
        handle, "pc1",
        PlayerIntent(intent_type="cast_spell", spell_id=slug, target_id="m1", **kw),
    ))
    return drain_pending_events(handle)


def _damage(events):
    return [(e.amount, e.damage_type) for e in events if e.type == "damage_applied"]


def _failures(events):
    return [e.reason for e in events if e.type == "cast_failed"]


def _slots(handle):
    live = get_live(handle)
    return dict((getattr(live, "spell_slots_by_entity", {}) or {}).get("pc1", {}))


def test_chosen_type_reaches_the_damage_roll():
    events = _cast(_fight(), SORCEROUS, damage_type="fire")
    assert [t for _, t in _damage(events)] == ["fire"]


def test_a_different_choice_gives_a_different_type():
    events = _cast(_fight(), SORCEROUS, damage_type="psychic")
    assert [t for _, t in _damage(events)] == ["psychic"]


def test_chromatic_orb_honours_the_choice():
    events = _cast(_fight(), CHROMATIC, damage_type="thunder")
    assert [t for _, t in _damage(events)] == ["thunder"]


def test_without_a_choice_the_first_listed_type_is_used():
    events = _cast(_fight(), SORCEROUS)
    assert [t for _, t in _damage(events)] == ["acid"]


def test_a_type_the_spell_does_not_offer_is_refused():
    events = _cast(_fight(), SORCEROUS, damage_type="bogus")
    assert _failures(events) == ["damage_type_invalid"]
    assert _damage(events) == []


def test_a_refused_type_is_not_applied_at_all():
    """No damage means the cast did not resolve: the state is untouched."""
    handle = _fight()
    before = _slots(handle)
    _cast(handle, SORCEROUS, damage_type="bogus")
    assert _slots(handle) == before


def test_every_offered_type_is_accepted():
    """An option the engine publishes must be castable: a refusal here means
    the engine offers a type it then rejects."""
    for damage_type in OFFERED:
        events = _cast(_fight(), SORCEROUS, damage_type=damage_type)
        assert _failures(events) == [], damage_type
        assert [t for _, t in _damage(events)] == [damage_type], damage_type


def test_a_two_activity_spell_needs_an_explicit_choice():
    """Spirit Guardians carries its damage on one activity and its 15-foot
    Emanation on another. Without activity_id the engine must refuse and
    say WHICH rule stopped it, not blame the target."""
    handle = _fight()
    events = asyncio.run(_cast_raw(handle, "spirit-guardians", damage_type="radiant"))
    assert _failures(events) == ["activity_ambiguous"]
    assert _damage(events) == []


def test_choosing_the_damaging_activity_makes_it_resolve():
    events = asyncio.run(_cast_raw(
        _fight(), "spirit-guardians", damage_type="radiant",
        activity_id="dnd5eactivity000", target_id="m1",
    ))
    assert _failures(events) == []
    assert [(t) for _, t in _damage(events)] == ["radiant"]


async def _cast_raw(handle, slug, **kw):
    await submit_player_intent(
        handle, "pc1",
        PlayerIntent(intent_type="cast_spell", spell_id=slug, **kw),
    )
    return drain_pending_events(handle)
    