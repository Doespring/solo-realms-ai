"""A printed reward must be booked, and an unpriced one must not be banked as zero."""
import pytest

from solo.nat20_campaign import CampaignSource, Reward
from solo.nat20_worker import _milestone_mode
from solo.progression import book_progression


def source():
    return CampaignSource(id='hoard-2014', version='1', note='Печатная награда книги')


def test_native_reward_refuses_a_supplied_amount():
    with pytest.raises(ValueError):
        Reward(id='r', kind='native_encounter_xp', source=source(), experience=50)


def test_a_printed_reward_keeps_its_value_and_page():
    reward = Reward(id='temple', kind='printed_experience', source=source(), experience=100, page=14)
    assert reward.experience == 100
    assert reward.page == 14


def test_a_printed_reward_cannot_be_silently_unpriced():
    with pytest.raises(ValueError):
        Reward(id='temple', kind='printed_experience', source=source())


def test_milestone_mode_is_read_from_the_definition_not_guessed():
    class Definition:
        progression = book_progression()
    assert _milestone_mode(Definition()) is True

    class Experience:
        progression = book_progression()
    Experience.progression = book_progression().model_copy(update={"mode": "experience"})
    assert _milestone_mode(Experience()) is False


def test_a_definition_without_progression_is_not_milestone():
    assert _milestone_mode(object()) is False
    assert _milestone_mode(None) is False
