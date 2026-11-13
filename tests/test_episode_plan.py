"""Both chapters are data on one shared path, not two hand-written programs."""
import inspect
import os
os.environ.setdefault('SOLO_TEST', '1')

import pytest

from solo import episode_plan, first_episode, second_episode


def test_the_shared_path_owns_the_structural_work():
    source = inspect.getsource(episode_plan)
    for step in ('WorldLocation(', 'WorldNode(', 'WorldRoute(', 'ScenarioEvent(', 'AdventureDefinition('):
        assert step in source, step


def test_a_chapter_module_no_longer_builds_world_objects_by_hand():
    for module in (first_episode, second_episode):
        source = inspect.getsource(module)
        assert 'ScenarioPackage(' not in source, module.__name__
        assert 'WorldRoute(' not in source, module.__name__
        assert 'ScenarioEvent(' not in source, module.__name__


def test_each_chapter_is_a_plan_of_data():
    for module in (first_episode, second_episode):
        plan = module.plan()
        assert isinstance(plan, episode_plan.EpisodePlan)
        assert plan.places and plan.fights
        assert plan.module.startswith("hoard-episode-")


def test_every_place_and_fight_can_name_its_page():
    plan = second_episode.plan()
    for place in plan.places:
        assert place.quote is None or 1 <= place.quote.page <= 2000
    for beat in plan.beats:
        assert 1 <= beat.page <= 2000
    for fight in plan.fights:
        assert 1 <= fight.page <= 2000


def test_the_printed_counts_survive_the_move_to_data():
    fight = second_episode.encounters()[second_episode.OUTPOST_ENCOUNTER]
    assert len(fight["actors"]) == second_episode.FOUR_CULTISTS + second_episode.EIGHT_KOBOLDS
    monsters = {a["monster"] for a in fight["actors"]}
    assert monsters == {"cultist", "kobold-warrior"}
    first = first_episode.encounter()
    assert len(first["actors"]) == first_episode.KOBOLDS


def test_a_chapter_cannot_link_to_a_place_it_did_not_declare():
    plan = second_episode.plan()
    data = plan.model_dump()
    data["links"].append(dict(source="camp", target="nowhere", meters=1, minutes=1))
    with pytest.raises(Exception):
        episode_plan.EpisodePlan.model_validate(data)


def test_a_chapter_cannot_place_a_beat_in_an_undeclared_place():
    plan = second_episode.plan()
    data = plan.model_dump()
    data["beats"].append(dict(id="lost", place="Нигде", page=17))
    with pytest.raises(Exception):
        episode_plan.EpisodePlan.model_validate(data)


def test_both_chapters_carry_the_book_progression():
    for module in (first_episode, second_episode):
        plan = module.plan()
        progression = plan.progression or __import__("solo.progression", fromlist=["book_progression"]).book_progression()
        assert progression.mode == "milestone"
