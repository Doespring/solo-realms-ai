"""Create Undead: сколько тел, какой формы, и только ночью."""
"""

SRD 5.2 Create Undead (level 6) has three obligations that pull in
different directions:

* the number of bodies depends on BOTH the slot and the form — "three
  Ghouls" at 6th, "two Ghasts or Wights" at 8th, "two Mummies" at 9th;
* the form is the player's choice, never guessed;
* "You can cast this spell only at night" is a fact about the WORLD, not
  about the caster.

That last one is why this file also covers the DM boundary (§8): the
engine cannot know whether it is night, so it must ASK and change
nothing until the answer arrives — asking is not an excuse for a missing
handler, and assuming would be a silent substitution of a rule the book
gives to the DM.
"""
import asyncio

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

#: The spell carries three activities (Animate Corpses, Reassert Control,
#: Mentally Command); the bodies come from the first.
ANIMATE = "ExyqLKqLhGbaJVVd"
NIGHT = "create-undead:night"


def _scores():
    return AbilityScores(strength=8, dexterity=14, constitution=14,
                         intelligence=18, wisdom=10, charisma=10)


def _fight(level=20, seed=1):
    """Level 20: slots 7-9 are needed to see the higher-level forms."""
    spec = CharacterBuildSpec(species_slug="human", class_slug="wizard",
                              background_slug="acolyte", level=level,
                              ability_scores=_scores())
    p = build_party_member(
        spec,
        CombatInstance(entity_id="pc1", name="P", zone_id="0,0", initiative=20,
                       hp_max=120, hp_current=120),
        loader=BundledAssetLoader(),
    )
    h = asyncio.run(O.start_combat(
        session_id="s", party=[p],
        encounter=[EncounterMemberSpec(entity_id="m1", name="T",
                                       entity_type="Monster", initiative=1,
                                       hp_current=80, hp_max=80, ac=12,
                                       zone_id="8,4")],
        grid_scene=GridScene(width=16, height=12), rng_seed=seed,
    )).handle
    O.drain_pending_events(h)
    return h


def _summons(h):
    live = O._get_live(h)
    return [c.entity_id for c in live.initiative if c.entity_id.startswith("summon:")]


def _slots(h):
    live = O._get_live(h)
    return {str(k): int(v) for k, v in dict(live.spell_slots_by_entity.get("pc1", {})).items()}


def _cast(h, slot=6, form="ghoul", night=True):
    extra = {} if night is None else {"adjudications": {NIGHT: night}}
    asyncio.run(O.submit_player_intent(
        h, "pc1",
        O.PlayerIntent(intent_type="cast_spell", spell_id="create-undead",
                       form_id=form, slot_level=slot, target_zone_id="2,0",
                       activity_id=ANIMATE, **extra),
    ))
    return O.drain_pending_events(h)


@pytest.mark.parametrize("slot,form,expected", [
    (6, "ghoul", 3),
    (7, "ghoul", 4),
    (8, "ghoul", 5),
    (8, "ghast", 2),
    (8, "wight", 2),
    (9, "ghast", 3),
    (9, "mummy", 2),
])
def test_the_slot_and_the_form_together_set_the_count(slot, form, expected):
    """«three Ghouls» / «two Ghasts or Wights» / «two Mummies» — по книге."""
    h = _fight()
    events = _cast(h, slot=slot, form=form)
    assert "spell_cast" in [e.type for e in events], [e.type for e in events]
    summons = _summons(h)
    assert len(summons) == expected, (slot, form, summons)
    assert {s.split(":")[2] for s in summons} == {form}, summons


def test_without_the_dm_answer_the_engine_asks_and_changes_nothing():
    """Ночь — обстоятельство мира. Движок спрашивает, а не предполагает.

    Слот и действие не тратятся: отказ приходит до расхода (§8 п.2).
    """
    h = _fight()
    before = _slots(h)
    events = _cast(h, slot=6, form="ghoul", night=None)
    asked = [e for e in events if e.type == "adjudication_requested"]
    assert len(asked) == 1, [e.type for e in events]
    assert asked[0].input_key == NIGHT, asked[0].input_key
    # Вопрос называет правило, а не только ключ.
    assert "night" in asked[0].question.lower(), asked[0].question
    assert "cast_failed" in [e.type for e in events], [e.type for e in events]
    assert _summons(h) == []
    assert _slots(h) == before, (before, _slots(h))


def test_a_negative_answer_is_honoured_like_no_answer_at_all():
    """Мастер сказал «не ночь» — тот же отказ, без частичных эффектов."""
    h = _fight()
    before = _slots(h)
    events = _cast(h, slot=6, form="ghoul", night=False)
    assert "adjudication_requested" in [e.type for e in events], [e.type for e in events]
    assert "cast_failed" in [e.type for e in events], [e.type for e in events]
    assert _summons(h) == []
    assert _slots(h) == before, (before, _slots(h))


def test_a_yes_answer_lets_the_cast_resolve_normally():
    """После ответа расчёт идёт штатным путём — не отдельной веткой."""
    h = _fight()
    before = _slots(h)
    events = _cast(h, slot=6, form="ghoul", night=True)
    assert "adjudication_requested" not in [e.type for e in events]
    assert "spell_cast" in [e.type for e in events], [e.type for e in events]
    assert len(_summons(h)) == 3
    assert _slots(h) != before, (before, _slots(h))


def test_the_answer_is_read_per_key_not_as_a_blanket_yes():
    """Ответ засчитывается только под своим ключом, не любой правдой."""
    h = _fight()
    before = _slots(h)
    asyncio.run(O.submit_player_intent(
        h, "pc1",
        O.PlayerIntent(intent_type="cast_spell", spell_id="create-undead",
                       form_id="ghoul", slot_level=6, target_zone_id="2,0",
                       activity_id=ANIMATE,
                       adjudications={"something-else": True}),
    ))
    events = O.drain_pending_events(h)
    assert "adjudication_requested" in [e.type for e in events], [e.type for e in events]
    assert _summons(h) == []
    assert _slots(h) == before, (before, _slots(h))