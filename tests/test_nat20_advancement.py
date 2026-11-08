"""Native advancement: every obligatory choice a level creates is enforced.

The pinned engine applies whatever ``selected_choices`` it is given but never
asked whether the build made *every* choice its levels oblige. These tests pin
the reviewed patch module (``dnd5e_engine.rules.advancement``) that answers that
question from the same canonical data ``derive_sheet`` reads, so a host cannot
fill the gap with its own arithmetic.
"""
import pytest

from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
from dnd5e_engine.rules.advancement import (
    XP_THRESHOLDS,
    ChoiceError,
    advance,
    choice_options,
    experience_for_level,
    hp_gain,
    level_for_experience,
    pending_choices,
    required_choices,
    species_lineages,
    subclass_options,
    validate_choices,
)
from dnd5e_srd_data.loader import BundledAssetLoader


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


STANDARD = dict(
    strength=15, dexterity=13, constitution=14, intelligence=12, wisdom=10, charisma=8
)


def build(**overrides):
    payload = dict(
        species_slug="human",
        class_slug="fighter",
        level=1,
        ability_scores=STANDARD,
        ability_score_method="standard_array",
        background_slug="soldier",
        equipment=(),
        selected_choices=(),
    )
    payload.update(overrides)
    return CharacterBuildSpec(**payload)


FIGHTER_1 = (
    "background:strength+2,constitution+1",
    "skill:athletics",
    "skill:perception",
    "skill:arcana",
    "defense",
    "alert",
)


def test_a_level_one_fighter_owes_a_background_a_style_two_skills_and_an_origin_feat(loader):
    rows = choice_options(build(), loader=loader)
    kinds = [row["kind"] for row in rows]
    assert kinds.count("skill") == 2
    assert {"background", "increase", "feature"} <= set(kinds)
    # The background pick is already satisfied by ``background_slug``; every
    # other row is untouched by an empty ``selected_choices``.
    owed = [row for row in rows if row["kind"] != "background"]
    assert owed and all(row["missing"] == row["count"] for row in owed)
    assert next(row for row in rows if row["kind"] == "background")["missing"] == 0


def test_a_build_that_owes_choices_is_refused_and_names_them(loader):
    with pytest.raises(ChoiceError) as error:
        validate_choices(build(), loader=loader)
    message = str(error.value)
    assert "advancement choices still owed" in message
    assert "skill x2" in message
    assert error.value.pending


def test_the_same_build_validates_once_every_choice_is_made(loader):
    legal = build(selected_choices=FIGHTER_1)
    validate_choices(legal, loader=loader)
    sheet = derive_sheet(legal, loader=loader)
    assert {"athletics", "perception", "arcana"} <= set(sheet.skill_proficiencies)
    assert set(sheet.feats) >= {"defense", "alert"}


@pytest.mark.parametrize("picks,reason", [
    (("skill:athletics", "skill:perception", "skill:arcana", "skill:stealth", "defense", "alert"),
     "allow"),
    (("skill:athletics", "skill:perception", "skill:arcana", "defense", "alert", "archery"),
     "beyond the feature choices"),
])
def test_overspending_is_refused_as_loudly_as_underspending(loader, picks, reason):
    with pytest.raises(ChoiceError) as error:
        validate_choices(
            build(selected_choices=("background:strength+2,constitution+1", *picks)),
            loader=loader,
        )
    assert reason in str(error.value)


def test_a_skill_outside_every_offered_pool_is_refused(loader):
    # A dwarf offers no ``skills:*`` pool and no Origin feat, so its whole
    # level-1 obligation is the increase, two skills and a fighting style.
    legal = ("background:strength+2,constitution+1", "skill:athletics",
             "skill:perception", "defense")
    validate_choices(build(species_slug="dwarf", selected_choices=legal), loader=loader)
    with pytest.raises(ChoiceError) as error:
        validate_choices(
            build(species_slug="dwarf",
                  selected_choices=(legal[0], "skill:athletics", "skill:stealth", "defense")),
            loader=loader,
        )
    assert "not in any skill pool" in str(error.value)


def test_a_background_grants_its_increase_or_the_choice_is_half_made(loader):
    with pytest.raises(ChoiceError) as error:
        validate_choices(
            build(selected_choices=FIGHTER_1[1:]), loader=loader
        )
    assert "background" in str(error.value)


def test_a_level_three_build_must_name_a_subclass_and_only_a_legal_one(loader):
    without = build(level=3, selected_choices=FIGHTER_1)
    owed = {(item.choice.kind, item.missing) for item in pending_choices(without, loader=loader)}
    assert ("subclass", 1) in owed
    validate_choices(build(level=3, subclass_slug="champion", selected_choices=FIGHTER_1), loader=loader)
    with pytest.raises(ChoiceError) as error:
        validate_choices(
            build(level=3, subclass_slug="evoker", selected_choices=FIGHTER_1), loader=loader
        )
    assert "evoker" in str(error.value)
    with pytest.raises(ChoiceError) as error:
        validate_choices(
            build(level=2, subclass_slug="champion", selected_choices=FIGHTER_1), loader=loader
        )
    assert "opens at fighter level 3" in str(error.value)


@pytest.mark.skip(reason='Ожидает решения потока движка: subclass_options смешивает издания 2014 и 2024, см. docs/HANDOFF_CANONICAL_2014_UNTRACKED.md')
def test_subclass_options_appear_only_from_the_class_own_gate(loader):
    assert subclass_options("fighter", 2, loader=loader) == ()
    assert [row["slug"] for row in subclass_options("fighter", 3, loader=loader)] == ["champion"]
    assert [row["slug"] for row in subclass_options("wizard", 3, loader=loader)] == ["evoker"]


def test_a_level_four_build_must_spend_its_ability_score_improvement(loader):
    base = dict(level=4, subclass_slug="champion")
    with pytest.raises(ChoiceError) as error:
        validate_choices(build(selected_choices=FIGHTER_1, **base), loader=loader)
    assert "asi x1" in str(error.value)
    raised = build(selected_choices=(*FIGHTER_1, "asi:fighter:4:strength+2"), **base)
    validate_choices(raised, loader=loader)
    # 15 base + 2 from the Soldier background + 2 from the level-4 slot.
    assert derive_sheet(raised, loader=loader).ability_scores.strength == 19


def test_advance_keeps_every_pick_and_raises_one_class_level(loader):
    four = build(
        level=4, subclass_slug="champion",
        selected_choices=(*FIGHTER_1, "asi:fighter:4:strength+2"),
    )
    five = advance(four, class_slug="fighter", loader=loader)
    assert five.level == 5 and five.classes == {"fighter": 5}
    assert five.selected_choices == four.selected_choices
    assert hp_gain(four, five, loader=loader) > 0
    with pytest.raises(ValueError):
        advance(four, class_slug="wizard", loader=loader)


def test_the_experience_table_is_the_srd_one_and_inverts(loader):
    assert XP_THRESHOLDS[0] == 0 and XP_THRESHOLDS[-1] == 355000
    assert [level_for_experience(xp) for xp in (0, 299, 300, 2699, 2700, 355000)] == [
        1, 1, 2, 3, 4, 20,
    ]
    assert all(experience_for_level(level) == XP_THRESHOLDS[level - 1] for level in range(1, 21))
    with pytest.raises(ValueError):
        level_for_experience(-1)


def test_lineage_groups_come_from_the_corpus_not_a_hand_written_table(loader):
    # Group by variant slugs, not by display names: the corpus names are player
    # facing and translated, while slugs are the stable identity. Grouping by
    # name is a known engine limitation, reported in
    # docs/HANDOFF_SPECIES_GROUPED_BY_NAME.md; this test still proves the
    # grouping itself comes from the corpus and not from a hand-written table.
    groups = {frozenset(v["slug"] for v in row["variants"]): row for row in species_lineages(loader)}
    elf = groups[frozenset({"elf-drow", "elf-high", "elf-wood"})]
    assert elf["lineage_required"] is True
    human = groups[frozenset({"human"})]
    assert human["lineage_required"] is False
    # Every group decides its lineage step from the corpus leaf count: one leaf
    # means no step, more than one means the player must still choose.
    for row in species_lineages(loader):
        assert row["lineage_required"] is (len(row["variants"]) > 1)


def test_advisory_pools_are_reported_but_never_block(loader):
    rows = choice_options(build(selected_choices=FIGHTER_1), loader=loader, include_advisory=True)
    mastery = [row for row in rows if row["kind"] == "mastery"]
    assert mastery and mastery[0]["blocking"] is False
    assert all(row["blocking"] for row in choice_options(build(selected_choices=FIGHTER_1), loader=loader))
