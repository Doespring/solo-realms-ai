"""In a milestone scenario the episode grants the level, not the fight."""
import pytest

from solo.progression import book_progression, MILESTONE


def definition_like(episode_id):
    class Definition:
        id = episode_id
        progression = book_progression()
    return Definition()


def level_the_episode_grants(definition):
    """The milestone level the worker resolves when none was passed in."""
    progression = definition.progression
    if progression.mode != MILESTONE:
        return None
    return progression.level_at(definition.id)


def test_episode_one_grants_level_two_without_any_experience():
    assert level_the_episode_grants(definition_like('hoard-episode-1')) == 2


def test_episode_two_grants_level_three():
    assert level_the_episode_grants(definition_like('hoard-episode-2')) == 3


def test_an_episode_the_book_leaves_open_grants_nothing():
    assert level_the_episode_grants(definition_like('hoard-episode-5')) is None


def test_the_worker_reads_the_definition_and_not_the_experience_counter():
    from solo.nat20_levelup import _advance_build
    source = open(_advance_build.__code__.co_filename, encoding="utf-8").read()
    body = source[source.index("def _advance_build"):]
    body = body[:body.index("return build, CharacterBuildSpec")]
    assert "progression" in body
    assert "level_at" in body
