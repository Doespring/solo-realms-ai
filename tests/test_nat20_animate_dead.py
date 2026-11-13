"""Animate Dead: одна кость за 3-й слот, три за 4-й, семь за 6-й.

SRD 5.2 Animate Dead raises "1 + @scaling.increase * 2" bodies, where
@scaling.increase counts the levels above the spell's base level. Two
things had to work for that: the formula had to be evaluable (the token
existed in no roll-data vocabulary), and one cast had to be able to seat
more than one creature (the seat was a single body). A spell that quietly
seated one where the book says seven would look implemented right up to
the moment a player counted the miniatures.

Checks run through the public interface: submit_player_intent + events.
"""
import asyncio

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader


def _scores():
    return AbilityScores(strength=8, dexterity=14, constitution=14,
                         intelligence=18, wisdom=10, charisma=10)


def _fight(level=11, seed=1):
    """A fresh fight: a second cast in the same fight hits target_invalid,
    which is a correct refusal but not what these checks are about."""
    spec = CharacterBuildSpec(species_slug="human", class_slug="wizard",
                              background_slug="acolyte", level=level,
                              ability_scores=_scores())
    p = build_party_member(
        spec,
        CombatInstance(entity_id="pc1", name="P", zone_id="0,0", initiative=20,
                       hp_max=60, hp_current=60),
        loader=BundledAssetLoader(),
    )
    h = asyncio.run(O.start_combat(
        session_id="s", party=[p],
        encounter=[EncounterMemberSpec(entity_id="m1", name="T",
                                       entity_type="Monster", initiative=1,
                                       hp_current=60, hp_max=60, ac=12,
                                       zone_id="8,4")],
        grid_scene=GridScene(width=16, height=12), rng_seed=seed,
    )).handle
    O.drain_pending_events(h)
    return h


def _summons(h):
    live = O._get_live(h)
    return [c.entity_id for c in live.initiative if c.entity_id.startswith("summon:")]


def _cast(h, slot, form="skeleton", cell="2,0"):
    asyncio.run(O.submit_player_intent(
        h, "pc1",
        O.PlayerIntent(intent_type="cast_spell", spell_id="animate-dead",
                       form_id=form, slot_level=slot, target_zone_id=cell),
    ))
    return O.drain_pending_events(h)


@pytest.mark.parametrize("slot,form,expected", [
    (3, "skeleton", 1),
    (4, "skeleton", 3),
    (5, "zombie", 5),
    (6, "zombie", 7),
])
def test_the_slot_level_sets_how_many_bodies_rise(slot, form, expected):
    """``1 + (slot - 3) * 2``: the book's own count formula, per level."""
    h = _fight()
    events = _cast(h, slot, form=form)
    assert "spell_cast" in [e.type for e in events], [e.type for e in events]
    summons = _summons(h)
    assert len(summons) == expected, (slot, summons)
    # Каждое тело — заявленной формы, а не случайный выбор за игрока.
    assert {s.split(":")[2] for s in summons} == {form}, summons


def test_the_bodies_are_distinct_creatures_not_one_seated_twice():
    """Семь тел — это семь разных существ в порядке инициативы.

    Идентификатор призыва нумеруется, поэтому повторная посадка одного и
    того же существа под двумя записями была бы видна сразу.
    """
    h = _fight()
    _cast(h, 6, form="zombie")
    summons = _summons(h)
    assert len(summons) == 7, summons
    assert len(set(summons)) == 7, summons
    live = O._get_live(h)
    # Каждое тело — отдельная запись ростера со своим здоровьем.
    bodies = [c for c in live.initiative if c.entity_id in set(summons)]
    assert len(bodies) == 7, [c.entity_id for c in bodies]
    assert all(b.hp_current > 0 for b in bodies), [(b.entity_id, b.hp_current) for b in bodies]


def test_an_unnamed_form_is_refused_rather_than_guessed():
    """Костей или труп — решает игрок; форма по умолчанию не выдумывается."""
    h = _fight()
    before = dict(O._get_live(h).spell_slots_by_entity.get("pc1", {}))
    events = O.drain_pending_events(h)
    asyncio.run(O.submit_player_intent(
        h, "pc1",
        O.PlayerIntent(intent_type="cast_spell", spell_id="animate-dead",
                       slot_level=3, target_zone_id="2,0"),
    ))
    events = O.drain_pending_events(h)
    assert "cast_failed" in [e.type for e in events], [e.type for e in events]
    assert _summons(h) == []
    after = dict(O._get_live(h).spell_slots_by_entity.get("pc1", {}))
    assert after == before, (before, after)


def test_a_slot_the_caster_does_not_have_is_refused():
    """Слот 9 у волшебника 11 уровня отсутствует — отказ, не тихая посадка."""
    h = _fight()
    events = _cast(h, 9, form="zombie")
    failures = [(e.type, getattr(e, "reason", None)) for e in events if e.type == "cast_failed"]
    assert failures and failures[0][1] == "no_slot", failures
    assert _summons(h) == []