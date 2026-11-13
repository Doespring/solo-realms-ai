"""SRD 5.2: Find Familiar — выбранная форма становится существом в бою."""

from __future__ import annotations

import asyncio
import itertools

import dnd5e_engine.orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

_LOADER = BundledAssetLoader()
_SESSION = itertools.count()


def _wizard_fight():
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="wizard",
        background_slug="acolyte",
        level=3,
        ability_scores=AbilityScores(
            strength=10,
            dexterity=14,
            constitution=14,
            intelligence=16,
            wisdom=12,
            charisma=10,
        ),
    )
    pc = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1",
            name="Волшебник",
            zone_id="0,0",
            initiative=20,
            hp_max=40,
            hp_current=40,
        ),
        loader=_LOADER,
    )
    pc = pc.model_copy(
        update={"spells_known": tuple(set(getattr(pc, "spells_known", ()) or ()) | {"find-familiar"})}
    )
    result = asyncio.run(
        O.start_combat(
            session_id=f"familiar-{next(_SESSION)}",
            party=[pc],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1",
                    name="Болван",
                    entity_type="Monster",
                    initiative=1,
                    hp_current=999,
                    hp_max=999,
                    ac=12,
                    zone_id="9,9",
                )
            ],
            grid_scene=GridScene(width=12, height=12),
            rng_seed=3,
        )
    )
    O.drain_pending_events(result.handle)
    return result.handle


def _familiar_vs_dummy():
    """Тот же волшебник, но болван стоит вплотную и с AC 2 — чтобы
    исход атаки фамильяра не зависел от броска."""
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="wizard",
        background_slug="acolyte",
        level=5,
        ability_scores=AbilityScores(
            strength=10,
            dexterity=14,
            constitution=14,
            intelligence=16,
            wisdom=14,
            charisma=10,
        ),
    )
    pc = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1",
            name="Волшебник",
            zone_id="0,0",
            initiative=20,
            hp_max=30,
            hp_current=30,
        ),
        loader=_LOADER,
    )
    pc = pc.model_copy(
        update={"spells_known": tuple(set(getattr(pc, "spells_known", ()) or ()) | {"find-familiar"})}
    )
    result = asyncio.run(
        O.start_combat(
            session_id=f"familiar-duel-{next(_SESSION)}",
            party=[pc],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1",
                    name="Болван",
                    entity_type="Monster",
                    initiative=1,
                    hp_current=999,
                    hp_max=999,
                    ac=2,
                    zone_id="1,0",
                )
            ],
            grid_scene=GridScene(width=12, height=12),
            rng_seed=3,
        )
    )
    O.drain_pending_events(result.handle)
    return result.handle


def _pass_turn(handle):
    asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(intent_type="pass")))
    return O.drain_pending_events(handle)

def _cast(handle, **kwargs):
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            O.PlayerIntent(intent_type="cast_spell", spell_id="find-familiar", **kwargs),
        )
    )
    return O.drain_pending_events(handle)


def test_the_chosen_form_joins_the_fight_with_its_own_stat_block():
    # «the familiar has the statistics of the chosen form». Сове из корпуса
    # соответствуют 1 hit point, AC 11 и Fly 60 ft — это не догадка, а
    # статблок owl.
    handle = _wizard_fight()
    events = _cast(handle, form_id="owl")
    joined = [e for e in events if e.type == "combatant_joined"]
    assert len(joined) == 1, [e.type for e in events]
    assert joined[0].stat_block_slug == "owl"
    assert joined[0].origin_caster_id == "pc1"
    assert joined[0].hp_max == 1
    owl = next(c for c in O._get_live(handle).initiative if c.entity_id == joined[0].entity_id)
    assert owl.ac == 11
    assert owl.movement_modes.fly == 60


def test_the_form_is_the_players_choice_and_never_a_default():
    # Форма не подставляется: без выбора движок отказывает ДО расхода слота
    # и действия (§8).
    handle = _wizard_fight()
    before = next(c for c in O._get_live(handle).initiative if c.entity_id == "pc1")
    events = _cast(handle)
    assert [getattr(e, "reason", None) for e in events if e.type == "cast_failed"] == [
        "target_invalid"
    ]
    after = next(c for c in O._get_live(handle).initiative if c.entity_id == "pc1")
    assert after.action_available == before.action_available
    assert not [e for e in events if e.type == "combatant_joined"]


def test_a_form_outside_the_book_list_is_refused_by_name():
    handle = _wizard_fight()
    events = _cast(handle, form_id="dragon")
    assert [getattr(e, "reason", None) for e in events if e.type == "cast_failed"] == [
        "target_invalid"
    ]


def test_every_form_the_book_names_has_a_stat_block_in_the_corpus():
    # «Bat, Cat, Frog, Hawk, Lizard, Octopus, Owl, Rat, Raven, Spider, Weasel».
    from dnd5e_engine.activities.conjuration import SUMMONS

    forms = SUMMONS["find-familiar"].forms
    assert sorted(forms) == [
        "bat",
        "cat",
        "frog",
        "hawk",
        "lizard",
        "octopus",
        "owl",
        "rat",
        "raven",
        "spider",
        "weasel",
    ]
    missing = [slug for slug in forms.values() if _LOADER.get_monster(slug) is None]
    assert missing == []


def test_the_slot_is_spent_only_for_a_form_that_resolved():
    handle = _wizard_fight()
    events = _cast(handle, form_id="bat")
    assert any(e.type == "spell_cast" for e in events)
    pc = next(c for c in O._get_live(handle).initiative if c.entity_id == "pc1")
    assert pc.action_available is False

def test_the_familiar_fights_with_its_own_stat_block():
    # «the familiar has the statistics of the chosen form»: Talons совы — её
    # собственный статблочный приём, а не оружие и не удар персонажа.
    handle = _familiar_vs_dummy()
    _cast(handle, form_id="owl")
    _pass_turn(handle)
    familiar = "summon:pc1:owl:1"
    assert O._get_live(handle).current_actor_id == familiar
    asyncio.run(
        O.submit_player_intent(
            handle,
            familiar,
            O.PlayerIntent(intent_type="attack", target_id="m1", stat_block_action_id="talons"),
        )
    )
    events = O.drain_pending_events(handle)
    kinds = [e.type for e in events]
    assert "attack_rolled" in kinds, kinds
    assert "damage_applied" in kinds, kinds
    assert [e.reason for e in events if e.type == "attack_failed"] == []


def test_the_familiar_cannot_swing_a_weapon_or_grapple():
    # У неё нет ни оружия, ни Unarmed Strike: её атаки — только статблочные
    # (§7: успешный статус без механического исхода не считается).
    handle = _familiar_vs_dummy()
    _cast(handle, form_id="owl")
    _pass_turn(handle)
    familiar = "summon:pc1:owl:1"
    asyncio.run(
        O.submit_player_intent(
            handle,
            familiar,
            O.PlayerIntent(intent_type="attack", target_id="m1"),
        )
    )
    events = O.drain_pending_events(handle)
    assert [e.reason for e in events if e.type == "attack_failed"] == ["action_unavailable"]
    assert "damage_applied" not in [e.type for e in events]
