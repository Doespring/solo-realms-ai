"""Every PHB class develops to level 20 through the public advancement API.

The existing advancement tests stop at level four. The book promises twenty, so
this drives each class from 1 to 20 answering every obligatory choice the engine
reports, then validates the finished build. It also pins the refusals: a build
that skips a choice is rejected, and an ability increase past 20 is rejected.
"""
import pytest

from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
from dnd5e_engine.rules.advancement import (
    ChoiceError,
    advance,
    pending_choices,
    subclass_gate,
    subclass_options,
    validate_choices,
)
from dnd5e_srd_data.loader import BundledAssetLoader


CLASSES = (
    "barbarian", "bard", "cleric", "druid", "fighter", "monk",
    "paladin", "ranger", "rogue", "sorcerer", "warlock", "wizard",
)

STANDARD = dict(
    strength=15, dexterity=13, constitution=14, intelligence=12, wisdom=10, charisma=8
)

# The increases a level-20 build can legally take: strength and constitution
# are already near the cap, so the later picks go to the other scores.
ASI_PLAN = {
    4: "strength+2", 8: "constitution+2", 12: "dexterity+2",
    16: "wisdom+2", 19: "charisma+2", 6: "dexterity+2", 14: "intelligence+2",
}


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def _answer(spec, pending, loader):
    """Apply one legal pick for each obligation the engine reports."""
    choices = list(spec.selected_choices)
    for row in pending:
        choice = row.choice
        if choice.kind == "asi":
            picks = ASI_PLAN.get(choice.level, "intelligence+1,charisma+1")
            token = "asi:%s:%d:%s" % (choice.source.split(":")[-1], choice.level, picks)
            if token not in choices:
                choices.append(token)
        elif choice.kind == "skill":
            for slug in choice.pool:
                token = f"skill:{slug}"
                if token not in choices:
                    choices.append(token)
                    break
        elif choice.kind == "expertise":
            for slug in choice.pool:
                token = f"expertise:{slug}"
                if token not in choices:
                    choices.append(token)
                    break
        elif choice.kind == "feature":
            # A pool can owe more than one pick (Sorcerer metamagic asks for
            # two at level 10), so fill the whole count.
            added = 0
            for slug in choice.pool:
                if added >= row.missing:
                    break
                if slug not in choices:
                    choices.append(slug)
                    added += 1
        elif choice.kind == "mastery":
            for slug in choice.pool:
                if slug not in choices:
                    choices.append(slug)
                    break
        elif choice.kind in {"language", "tool"} or any(":*" in str(p) for p in choice.pool):
            # Wildcard pools (tool:music:*, languages:*) have no corpus entries,
            # so _resolve_pool returns the token unchanged and there is no slug
            # to select. They are advisory: record nothing and do not treat the
            # pool as an unanswerable obligation.
            continue
        else:
            for slug in choice.pool:
                if slug not in choices:
                    choices.append(slug)
                    break
    return spec.model_copy(update={"selected_choices": tuple(choices)})


@pytest.mark.parametrize("class_slug", CLASSES)
def test_every_class_reaches_level_twenty_with_valid_choices(class_slug, loader):
    gate = subclass_gate(class_slug, loader=loader) or 3
    options = subclass_options(class_slug, gate, loader=loader)
    assert options, f"{class_slug} offers no subclass to pick"
    subclass = options[0]["slug"]
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug=class_slug,
        level=1,
        ability_scores=STANDARD,
        ability_score_method="standard_array",
        background_slug="soldier",
        equipment=(),
        selected_choices=("background:strength+2,constitution+1",),
        subclass_slug=subclass,
    )
    def settle(current):
        """Answer every non-advisory obligation, however many rounds it takes."""
        for _ in range(12):
            pending = pending_choices(current, loader=loader)
            owed = [p for p in pending if p.choice.kind not in {"mastery", "language", "tool"}]
            if not owed:
                return current, pending
            current = _answer(current, pending, loader)
        raise AssertionError((class_slug, [(p.choice.kind, p.missing, p.choice.title, p.choice.pool[:3], p.choice.level) for p in pending]))

    spec, _ = settle(spec)
    for _ in range(1, 20):
        spec = advance(spec, class_slug=class_slug, loader=loader)
        spec, _ = settle(spec)
    assert spec.level == 20
    pending = pending_choices(spec, loader=loader)
    if any(p.choice.kind not in {"mastery", "language", "tool"} for p in pending):
        spec = _answer(spec, pending, loader)
    validate_choices(spec, loader=loader)
    sheet = derive_sheet(spec, loader=loader)
    assert sheet.hp_max > 0
    assert sheet.proficiency_bonus == 6
    # A chosen damage type has to reach the sheet, not just satisfy the
    # obligation: Draconic Sorcery picks one at level 6 with a dr: pool.
    chosen = [c for c in spec.selected_choices if c.startswith(("dr:", "di:"))]
    for token in chosen:
        kind, _, damage = token.partition(":")
        target = sheet.damage_resistances if kind == "dr" else sheet.damage_immunities
        assert damage in target, (class_slug, token, target)


def test_skipping_an_obligation_is_refused(loader):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", level=1, ability_scores=STANDARD,
        ability_score_method="standard_array", background_slug="soldier",
        equipment=(), selected_choices=("background:strength+2,constitution+1",),
        subclass_slug="champion",
    )
    for _ in range(1, 20):
        spec = advance(spec, class_slug="fighter", loader=loader)
    with pytest.raises(ChoiceError):
        validate_choices(spec, loader=loader)


def test_an_increase_past_twenty_is_refused(loader):
    # Strength starts at 15 and the background adds 2, so three +2 increases
    # would reach 23: the third one is refused, not silently clamped.
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", level=8, ability_scores=STANDARD,
        ability_score_method="standard_array", background_slug="soldier",
        equipment=(),
        selected_choices=(
            "background:strength+2,constitution+1", "asi:fighter:4:strength+2",
            "asi:fighter:6:strength+2", "asi:fighter:8:strength+2",
        ),
        subclass_slug="champion",
    )
    with pytest.raises(ValueError, match="stop at 20"):
        derive_sheet(spec, loader=loader)

