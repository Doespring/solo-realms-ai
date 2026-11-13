"""Giant Insect: все три формы читают один статблок с числами от слота.

SRD 5.2 Giant Insect: "You summon a giant centipede, spider, or wasp
(chosen when you cast the spell) ... and uses the Giant Insect stat
block. The form you choose determines certain details in its stat
block." Особенность этого заклинания в том, что формы НЕ дают разных
статблоков — они уточняют детали одного. Слот же масштабирует сам статблок:
"AC 11 + the spell level" и "HP 30 + 10 for each spell level above 4".

Проверяется, что масштабирование доходит до посаженного существа, а не
остаётся в JSON: AC и HP берутся из события combatant_joined.
"""
import asyncio

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

FORMS = ("giant-centipede", "giant-spider", "giant-wasp")


def _scores():
    return AbilityScores(strength=8, dexterity=14, constitution=14,
                         intelligence=18, wisdom=10, charisma=10)


def _fight(level=11, seed=1):
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


def _cast(h, slot=4, form="giant-centipede"):
    extra = {} if form is None else {"form_id": form}
    asyncio.run(O.submit_player_intent(
        h, "pc1",
        O.PlayerIntent(intent_type="cast_spell", spell_id="giant-insect",
                       slot_level=slot, target_zone_id="2,0", **extra),
    ))
    return O.drain_pending_events(h)


def _joined(events):
    return [e for e in events if e.type == "combatant_joined"]


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("slot,ac,hp", [(4, 15, 30), (5, 16, 40), (6, 17, 50)])
def test_the_slot_scales_ac_and_hp_for_every_form(form, slot, ac, hp):
    """AC 11 + уровень; HP 30 + 10 за каждый уровень выше 4-го."""
    h = _fight()
    events = _cast(h, slot=slot, form=form)
    assert "spell_cast" in [e.type for e in events], [e.type for e in events]
    joined = _joined(events)
    assert len(joined) == 1, joined
    assert joined[0].ac == ac, (form, slot, joined[0].ac)
    assert joined[0].hp_max == hp, (form, slot, joined[0].hp_max)
    assert joined[0].stat_block_slug == "giant-insect", joined[0].stat_block_slug


def test_an_unnamed_or_unknown_form_is_refused():
    """Форма выбирается при касте; угадывать её или принимать чужую нельзя."""
    for form in (None, "giant-scorpion"):
        h = _fight()
        before = dict(O._get_live(h).spell_slots_by_entity.get("pc1", {}))
        events = _cast(h, slot=4, form=form)
        assert "cast_failed" in [e.type for e in events], (form, [e.type for e in events])
        assert "spell_cast" not in [e.type for e in events], form
        assert _joined(events) == [], form
        after = dict(O._get_live(h).spell_slots_by_entity.get("pc1", {}))
        assert after == before, (form, before, after)