"""Требование 6: укрытие даёт +2/+5 к спасброску ЛОВ, но не к СИЛ.

SRD 5.2 Cover: half cover - "+2 bonus to Dexterity saving throws";
three-quarters cover - "+5 bonus"; spell with ignore_cover (Sacred Flame)
пробивает укрытие. Бонус относится к спасброску ТОЛЬКО ЛОВ.

Проверка идёт через roll_save с ОДНИМ и тем же зерном (d20 = 11), поэтому
меняется единственная переменная - степень укрытия.
"""
import asyncio
import random

from dnd5e_engine import orchestrator as O
from dnd5e_engine.activities.context import ActivityResolutionContext
from dnd5e_engine.activities.save_primitive import cover_bonus, roll_save
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=10, dexterity=14, constitution=12,
    intelligence=10, wisdom=10, charisma=10,
)


def _fighter():
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="soldier",
        level=1, ability_scores=SCORES, selected_choices=("archery",),
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Fighter", zone_id="0,0",
            initiative=10, hp_max=20, hp_current=20,
        ),
        loader=BundledAssetLoader(),
    )
    handle = asyncio.run(
        O.start_combat(
            session_id="cover", party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=99, hp_max=99, ac=12, zone_id="2,0",
                )
            ],
            grid_scene=GridScene(width=8, height=6), rng_seed=11,
        )
    ).handle
    live = O._get_live(handle)
    return next(c for c in live.initiative if c.entity_id == "pc1")


def _ctx(fighter, cover):
    abilities = {
        "str": fighter.strength, "dex": fighter.dexterity, "con": fighter.constitution,
        "int": fighter.intelligence, "wis": fighter.wisdom, "cha": fighter.charisma,
    }
    return ActivityResolutionContext(
        rng=random.Random(7), caster=fighter, targets=[fighter],
        event_emitter=lambda event: None, caster_abilities=abilities,
        target_cover={"pc1": cover},
    )


def test_cover_bonus_values_match_the_source():
    """+2 / +5 / 0 — значения из SRD, не из головы."""
    assert cover_bonus("half") == 2
    assert cover_bonus("three_quarters") == 5
    assert cover_bonus("none") == 0


def test_half_and_three_quarters_cover_reach_the_dex_save():
    fighter = _fighter()
    none = roll_save(_ctx(fighter, "none"), fighter, "dex", 15)
    half = roll_save(_ctx(fighter, "half"), fighter, "dex", 15)
    three = roll_save(_ctx(fighter, "three_quarters"), fighter, "dex", 15)
    assert none.modifier == 0, f"без укрытия модификатор {none.modifier}"
    assert half.modifier - none.modifier == 2, "половинное укрытие не дало +2"
    assert three.modifier - none.modifier == 5, "укрытие на 3/4 не дало +5"


def test_cover_does_not_help_a_strength_save():
    """SRD: бонус относится только к ЛОВ."""
    fighter = _fighter()
    plain = roll_save(_ctx(fighter, "none"), fighter, "str", 15)
    covered = roll_save(_ctx(fighter, "three_quarters"), fighter, "str", 15)
    assert covered.modifier == plain.modifier, (
        "укрытие повысило спасбросок СИЛ, хотя правило говорит только о ЛОВ"
    )


def test_a_spell_that_ignores_cover_ignores_it():
    """Sacred Flame: `ignore_cover: true` — укрытие не помогает от него."""
    fighter = _fighter()
    covered = roll_save(_ctx(fighter, "none"), fighter, "dex", 15, ignore_cover=False)
    ignored = roll_save(_ctx(fighter, "three_quarters"), fighter, "dex", 15, ignore_cover=True)
    assert ignored.modifier == covered.modifier, (
        "заклинание с ignore_cover всё равно получило бонус укрытия"
    )

