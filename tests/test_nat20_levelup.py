"""Level-up: one atomic advancement through the same admissibility path."""
import pytest

from solo.nat20_levelup import level_up

from nat20_builds import legal


def initialized(**payload):
    """A committed experimental campaign state, as the worker builds it."""
    from solo.nat20_content import release
    from solo.nat20_prototype import RELEASE, make_definition
    from solo.nat20_worker import initialize

    build = legal(
        class_slug=payload.pop("class_slug", "fighter"),
        species_slug="human",
        background=payload.pop("background", "soldier"),
        **payload,
    )
    definition = make_definition(build, {}, [], [], "Проверка", "Проверка повышения")
    loader, fingerprint, _ = release(RELEASE)
    state = initialize(
        dict(mode="initialize", name="Арден", seed=11,
             definition=definition.model_dump(mode="json")),
        loader,
        fingerprint,
    )
    return state, loader, fingerprint


def advance(state, loader, fingerprint, **payload):
    return level_up(dict(state=state, **payload), loader, fingerprint)


def test_a_level_up_raises_the_native_sheet_and_the_definition():
    state, loader, fingerprint = initialized()
    result = advance(state, loader, fingerprint, class_slug="fighter", milestone=2)
    after = result["state"]
    assert result["result"]["from_level"] == 1
    assert result["result"]["to_level"] == 2
    assert after["definition"]["build"]["level"] == 2
    assert after["sheet"]["hp_max"] == result["result"]["hp_max"]
    assert after["hit_dice"]["dice_total"] == 2


def test_a_level_up_does_not_heal_the_hero():
    state, loader, fingerprint = initialized()
    wounded = state["hero"]["hp_current"]
    result = advance(state, loader, fingerprint, class_slug="fighter", milestone=2)
    after = result["state"]
    assert after["hero"]["hp_current"] == wounded
    assert after["hero"]["hp_max"] == result["result"]["hp_max"]
    assert result["result"]["hp_gain"] > 0


def test_a_level_up_without_earned_progress_is_refused():
    state, loader, fingerprint = initialized(level=2)
    with pytest.raises(ValueError, match="Нет доступного повышения"):
        advance(state, loader, fingerprint, class_slug="fighter")


def test_the_same_level_up_applied_twice_grants_one_level():
    state, loader, fingerprint = initialized()
    once = advance(state, loader, fingerprint, class_slug="fighter", milestone=2)["state"]
    with pytest.raises(ValueError, match="Нет доступного повышения"):
        advance(once, loader, fingerprint, class_slug="fighter")


def test_a_milestone_assigns_one_level_per_confirmation():
    """A chapter that unlocks level 3 is still confirmed one level at a time.

    The milestone stays earned after the first confirmation, so the second
    confirmation continues to 3 and asks for the subclass that level opens
    (owner, L03: advancement inside the game goes one step, never a jump).
    """
    state, loader, fingerprint = initialized()
    first = advance(state, loader, fingerprint, class_slug="fighter", milestone=3)["state"]
    assert first["definition"]["build"]["level"] == 2
    assert first["definition"]["build"].get("subclass_slug") is None
    second = advance(
        first, loader, fingerprint,
        class_slug="fighter", milestone=3, subclass_slug="champion",
    )["state"]
    assert second["definition"]["build"]["level"] == 3
    assert second["hit_dice"]["dice_total"] == 3
    assert second["definition"]["build"]["subclass_slug"] == "champion"


def test_a_class_the_hero_lacks_is_refused():
    state, loader, fingerprint = initialized()
    with pytest.raises(ValueError, match="не входит в этого героя"):
        advance(state, loader, fingerprint, class_slug="wizard")


def test_a_level_up_that_opens_a_subclass_stays_uncommittable_without_it():
    state, loader, fingerprint = initialized()
    two = advance(state, loader, fingerprint, class_slug="fighter", milestone=2)["state"]
    # The refusal asks the player in the sheet's words (r188); the level still
    # cannot be committed until the choice is made.
    with pytest.raises(ValueError, match="подкласс"):
        advance(two, loader, fingerprint, class_slug="fighter", milestone=3)
    done = advance(
        two, loader, fingerprint,
        class_slug="fighter", milestone=3, subclass_slug="champion",
    )["state"]
    assert done["definition"]["build"]["subclass_slug"] == "champion"


def test_a_level_up_into_a_slot_level_keeps_spent_slots_spent():
    state, loader, fingerprint = initialized(class_slug="wizard", background="sage")
    before = dict(state["hero"]["spell_slots"])
    # A wizard reaches Expertise at level 2, so the player supplies that pick.
    target = legal(
        class_slug="wizard", species_slug="human", background="sage", level=2
    )
    after = advance(
        state, loader, fingerprint,
        class_slug="wizard", milestone=2,
        selected_choices=list(target["selected_choices"]),
    )["state"]
    fresh = dict(after["hero"]["spell_slots"])
    assert fresh != before
    assert all(
        int(fresh.get(level, 0)) >= int(count)
        for level, count in before.items()
    )


def test_a_level_up_keeps_the_campaign_reward_ledger():
    state, loader, fingerprint = initialized()
    after = advance(state, loader, fingerprint, class_slug="fighter", milestone=2)["state"]
    assert after["rewards"] == state["rewards"]
    assert after["experience"] == state["experience"]


def test_a_level_up_during_combat_is_refused():
    state, loader, fingerprint = initialized()
    state["combat"] = {"view": {}, "active_effects": []}
    with pytest.raises(ValueError, match="во время боя"):
        advance(state, loader, fingerprint, class_slug="fighter", milestone=2)


def test_an_unknown_prepared_spell_is_refused():
    state, loader, fingerprint = initialized(class_slug="wizard")
    with pytest.raises(ValueError):
        advance(
            state, loader, fingerprint,
            class_slug="wizard", milestone=2, prepared_spells=["fireball"],
        )