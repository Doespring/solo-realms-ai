"""The wizard contract: one admissibility path for creation and level-up."""
import pytest

from solo.nat20_wizard import (
    WizardDraft, contract, level_up_draft, suggested_build, suggested_draft,
)

from nat20_builds import legal


def draft(**payload):
    return WizardDraft(**payload)


def fighter(**payload):
    """A complete legal fighter, filled from the native pools."""
    build = legal(class_slug="fighter", species_slug="human", background="soldier")
    fields = dict(
        species_slug="human",
        class_slug="fighter",
        background_slug="soldier",
        ability_score_method="standard_array",
        ability_scores=build["ability_scores"],
        selected_choices=list(build["selected_choices"]),
        equipment_kits={"class:fighter": "A", "background:soldier": "A"},
        name="Арден",
    )
    fields.update(payload)
    return draft(**fields)


def step(state, name):
    return next(row for row in state["steps"] if row["id"] == name)


def test_an_empty_draft_offers_the_first_step_only():
    state = contract(draft())
    assert state["ready"] is False
    assert state["steps"][0]["id"] == "species"
    assert state["steps"][0]["status"] == "empty"
    assert state["blocking"]


def test_species_is_required_before_a_sheet_exists():
    state = contract(draft())
    assert state["sheet"] is None
    assert any("вид" in issue for issue in state["issues"])


def test_a_finished_fighter_is_ready_and_derives_a_sheet():
    state = contract(fighter())
    assert state["issues"] == []
    assert state["blocking"] == []
    assert state["ready"] is True
    assert state["sheet"]["hp_max"] > 0
    assert state["sheet"]["ac"] > 0


def test_the_steps_follow_the_fixed_order():
    ids = [row["id"] for row in contract(fighter())["steps"]]
    assert ids[0] == "species"
    assert ids.index("class") < ids.index("background")
    assert ids.index("scores") < ids.index("skills")
    assert ids[-1] == "final"


def test_a_lineage_step_appears_only_for_a_split_species():
    human = [row["id"] for row in contract(fighter())["steps"]]
    assert "lineage" not in human
    elf = contract(fighter(species_slug="elf-high"))
    assert [row["id"] for row in elf["steps"]][:2] == ["species", "lineage"]


@pytest.mark.skip(reason='Ожидает решения потока движка: subclass_options смешивает издания 2014 и 2024, см. docs/HANDOFF_CANONICAL_2014_UNTRACKED.md')
def test_a_subclass_step_appears_at_its_gate_level_only():
    low = contract(fighter(level=2))
    assert "subclass" not in [row["id"] for row in low["steps"]]
    high = contract(fighter(level=3))
    gate = step(high, "subclass")
    assert gate["opens_at_level"] == 3
    assert gate["status"] == "empty"
    assert [row["id"] for row in gate["options"]] == ["champion"]


def test_a_finished_hero_without_its_subclass_is_not_ready():
    state = contract(fighter(level=3))
    assert "subclass" in state["blocking"]
    assert any("subclass" in issue for issue in state["issues"])
    done = contract(fighter(level=3, subclass_slug="champion"))
    assert "subclass" not in done["blocking"]


def test_an_illegal_subclass_is_rejected_not_silently_dropped():
    state = contract(fighter(level=1, subclass_slug="champion"))
    assert "subclass" not in [row["id"] for row in state["steps"]]
    assert any("subclass" in issue for issue in state["issues"])


def test_an_unknown_species_is_reported_invalid():
    state = contract(fighter(species_slug="klingon"))
    assert step(state, "species")["status"] == "invalid"
    assert any("klingon" in reason for reason in step(state, "species")["reasons"])


def test_scores_without_a_generation_method_are_invalid():
    state = contract(fighter(ability_score_method=None))
    assert step(state, "scores")["status"] == "invalid"
    assert "scores" in state["blocking"]


def test_a_missing_choice_keeps_the_step_incomplete_with_its_count():
    build = legal(class_slug="fighter", species_slug="human", background="soldier")
    picks = [row for row in build["selected_choices"] if not row.startswith("skill:")]
    state = contract(fighter(selected_choices=picks))
    skills = [row for row in state["steps"] if row.get("kind") == "skill"]
    assert skills
    assert any(row["missing"] for row in skills)
    assert state["ready"] is False
    assert state["pending"]


def test_the_equipment_step_grants_letters_not_items():
    state = contract(fighter())
    equipment = step(state, "equipment")
    assert equipment["status"] == "complete"
    keys = {group["id"] for group in equipment["groups"]}
    assert keys == {"class:fighter", "background:soldier"}
    letters = {option["id"] for group in equipment["groups"] for option in group["options"]}
    assert letters == {"A", "B", "C"}
    assert any(option["recommended"] for group in equipment["groups"] for option in group["options"])


def test_an_unpicked_kit_keeps_the_step_open():
    state = contract(fighter(equipment_kits={}))
    assert step(state, "equipment")["status"] == "empty"
    assert "equipment" in state["blocking"]


def test_an_illegal_kit_letter_is_reported():
    state = contract(fighter(equipment_kits={"class:fighter": "Z"}))
    equipment = step(state, "equipment")
    assert any("Z" in reason for reason in equipment["reasons"])


def test_the_equipment_projection_sums_gold_and_lists_items():
    state = contract(fighter())
    equipment = state["equipment"]
    assert equipment["gold_gp"] == sum(row["gold_gp"] for row in equipment["grants"])
    assert len(equipment["items"]) >= 5
    assert {row["slug"] for row in equipment["items"]} >= {"chain-mail", "greatsword"}


@pytest.mark.parametrize(
    "class_slug,background",
    [("rogue", "soldier"), ("wizard", "sage"), ("cleric", "acolyte")],
)
def test_the_server_can_offer_a_legal_default_for_martial_and_caster(class_slug, background):
    """The advanced form opens on a default the server itself accepts.

    A default with owed choices would open the catalogue on a draft the next
    button refuses, and a martial-only default would leave a caster unable to
    start at all.
    """
    draft, state = suggested_draft(class_slug=class_slug, background_slug=background)
    assert state["ready"], state["issues"] or state["blocking"]
    assert not state["blocking"]
    assert state["build"]["class_slug"] == class_slug
    assert state["build"] == suggested_build(
        class_slug=class_slug, background_slug=background)


def test_a_caster_default_fills_the_published_magic_counts():
    """The magic step has no choice_key, so it needs its own answer."""
    draft, state = suggested_draft(class_slug="wizard", background_slug="sage")
    assert state["ready"], state["issues"] or state["blocking"]
    magic = next(row for row in state["steps"] if row["id"] == "magic")
    limits = {row["name"]: int(row["value"]) for row in magic["limits"]}
    assert magic["missing"] == 0
    assert len(magic["picked_cantrips"]) == limits.get("cantrips-known", 0)
    assert len(magic["picked_spells"]) == limits.get("max-prepared", 0)


def test_a_caster_level_up_asks_for_the_extra_prepared_spell():
    """A new spell slot is a new mandatory choice, not a silent grant.
    """
    draft, state = suggested_draft(class_slug="wizard", background_slug="sage", level=2)
    assert state["ready"], state["issues"]
    advanced = level_up_draft(draft)
    assert advanced["from_level"] == 2 and advanced["to_level"] == 3
    after = contract(advanced["draft"])
    assert after["ready"] is False
    open_steps = {
        row["id"]: row
        for row in after["steps"]
        if row["status"] != "complete"
    }
    assert "subclass" in open_steps
    magic = open_steps["magic"]
    assert magic["missing"] == 1
    assert "Заклинаний" in " ".join(magic["reasons"])

def test_the_gm_packet_marks_the_backstory_as_the_players_own_text():
    """Provenance, not just presence: the GM must be able to tell the
    player's own words from a fact it derived out of the source book.
    """
    from solo.game import self_knowledge

    hero = {"name": "Рив", "background": "Ищет мост."}
    campaign = {"state": {"hero": hero,
                          "preparation": {"background_confirmed": True,
                                        "source": "hero-backstory"}}}
    knowledge = self_knowledge(campaign, hero)
    assert knowledge["source"] == "hero-backstory"
    assert knowledge["background"] == "Ищет мост."
    assert knowledge["known_by"] == ["hero"]
    assert knowledge["text_kind"]

def test_a_martial_class_offers_no_magic_step():
    state = contract(fighter())
    assert "magic" not in [row["id"] for row in state["steps"]]


def test_a_wizard_magic_step_is_bounded_by_its_published_counts():
    build = legal(class_slug="wizard", species_slug="elf-high", background="sage")
    state = contract(draft(
        species_slug="elf-high",
        class_slug="wizard",
        background_slug="sage",
        ability_score_method="standard_array",
        ability_scores=build["ability_scores"],
        selected_choices=list(build["selected_choices"]),
        equipment_kits={"class:wizard": "A", "background:sage": "A"},
        name="Мира",
    ))
    magic = step(state, "magic")
    limits = {row["name"]: row["value"] for row in magic["limits"]}
    assert limits["cantrips-known"] == 3
    assert limits["max-prepared"] == 4
    assert magic["missing"] == 7
    assert magic["status"] == "empty"
    assert "magic" in state["blocking"]
    assert all(row["detail"].startswith("уровень ") for row in magic["options"])


def test_a_spell_above_the_available_slot_is_not_offered():
    state = contract(fighter())
    del state
    build = legal(class_slug="wizard", species_slug="human", background="sage")
    first = WizardDraft(
        species_slug="human",
        class_slug="wizard",
        background_slug="sage",
        ability_score_method="standard_array",
        ability_scores=build["ability_scores"],
        selected_choices=list(build["selected_choices"]),
    )
    magic = step(contract(first), "magic")
    assert magic["highest_slot"] == 1
    levels = {row["detail"] for row in magic["options"]}
    assert levels == {"уровень 0", "уровень 1"}


def test_a_level_up_reports_what_the_new_level_opens():
    state = level_up_draft(fighter(level=2))
    assert state["from_level"] == 2
    assert state["to_level"] == 3
    assert state["opened"] == ["class:fighter:subclass:3:Subclass"]
    assert state["draft"].level == 3


def test_a_level_up_projects_before_and_after_from_the_native_sheet():
    state = level_up_draft(fighter(level=2))
    assert state["before"]["hp_max"] < state["after"]["hp_max"]
    assert state["hp_gain"] == state["after"]["hp_max"] - state["before"]["hp_max"]


def test_a_level_up_keeps_every_existing_pick():
    source = fighter(level=2)
    state = level_up_draft(source)
    assert state["draft"].selected_choices == source.selected_choices
    assert state["draft"].equipment_kits == source.equipment_kits


def test_a_level_up_past_twenty_is_refused():
    with pytest.raises(ValueError):
        level_up_draft(fighter(level=20))


def test_a_level_up_without_a_class_is_refused():
    with pytest.raises(ValueError):
        level_up_draft(draft(species_slug="human"))
