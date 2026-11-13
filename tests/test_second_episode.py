"""Episode two keeps the book's story while Nat20 supplies the mechanics."""
from pathlib import Path
import shutil

import pytest
from solo.adventure_library import AdventureLibrary
from solo.second_episode import (CAMPFIRE, CONTACT, EIGHT_KOBOLDS, FOUR_CULTISTS,
                                 MODULE, OUTPOST, OUTPOST_ENCOUNTER, encounters,
                                 definition, verify_anchors)

PROFILE = Path.home() / 'AppData' / 'Local' / 'SoloRealmsAI-Prototype-1'


@pytest.fixture(scope='module')
def library(tmp_path_factory):
    target = tmp_path_factory.mktemp('episode-two')
    source = PROFILE / 'adventure.sqlite3'
    if not source.is_file():
        pytest.skip('Владелец не импортировал книгу в этом профиле')
    shutil.copy2(source, target / 'adventure.sqlite3')
    files = target / 'adventure-files'
    files.mkdir()
    for item in (PROFILE / 'adventure-files').iterdir():
        if item.is_file():
            shutil.copy2(item, files / item.name)
    return AdventureLibrary(target)


def test_pages_are_verified_before_the_module_is_built(library):
    assert verify_anchors(library) is True


def test_story_places_and_the_next_step_are_kept(library):
    module = definition(library, 'dnd2024', 1)
    names = [place.name for place in module.world.locations]
    assert names == ['Тропа к лагерю налетчиков', 'Застава отставших', 'Лагерь налетчиков']
    # The continuation is a registered module, so the first scene has an exit.
    assert module.continuation_of == ['hoard-episode-1']
    assert {route.model_dump()['to'] for route in module.world.routes} == {'outpost', 'campfire'}


def test_book_keeps_2014_while_mechanics_use_2024(library):
    module = definition(library, 'dnd2024', 1)
    assert module.source_ruleset == 'dnd2014'
    assert module.ruleset == 'dnd2024'


def test_encounter_matches_the_printed_count(library):
    # Page 17 fixes the composition: four human cultists and eight kobolds.
    actors = encounters()[OUTPOST_ENCOUNTER]['actors']
    kinds = [actor['monster'] for actor in actors]
    assert len(actors) == FOUR_CULTISTS + EIGHT_KOBOLDS
    assert kinds.count('cultist') == FOUR_CULTISTS
    assert kinds.count('kobold-warrior') == EIGHT_KOBOLDS
    assert len({actor['id'] for actor in actors}) == len(actors)


def test_no_level_ceiling_is_invented(library):
    # The verified pages state no ceiling, so none is asserted.
    assert definition(library, 'dnd2024', 1).maximum_level is None
