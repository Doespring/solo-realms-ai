"""Advancement belongs to the scenario: milestone or experience, never a guess."""
import pytest

from solo.progression import (
    EXPERIENCE, MILESTONE, PER_CHARACTER, SHARED, UNSET, Milestone, Progression,
    RewardRule, book_progression, for_definition, payout)


def test_the_book_chooses_milestone_and_names_its_pages():
    progression = book_progression()
    assert progression.mode == MILESTONE
    assert [(m.episode, m.level, m.page) for m in progression.milestones] == [
        ('hoard-episode-1', 2, 15),
        ('hoard-episode-2', 3, 22),
        ('hoard-episode-3', 4, 31),
        ('hoard-episode-4', 5, 42),
    ]


def test_the_fifth_episode_grants_no_level_because_page_seven_says_so():
    progression = book_progression()
    assert progression.level_at('hoard-episode-5') is None
    assert progression.level_at('hoard-episode-2') == 3


def test_every_printed_reward_carries_its_page_and_reason():
    progression = book_progression()
    assert len(progression.rewards) >= 10
    for reward in progression.rewards:
        assert 1 <= reward.page <= 2000
        assert reward.reason.strip()


def test_rewards_are_not_only_combat():
    earned = {r.earned_by for r in book_progression().rewards}
    assert 'social' in earned and 'exploration' in earned and 'story' in earned


def test_both_payout_bases_exist_because_the_book_uses_both():
    bases = {r.basis for r in book_progression().rewards}
    assert bases == {PER_CHARACTER, SHARED}


def test_experience_is_pooled_so_the_party_stays_one_level():
    rule = RewardRule(id="pooled", amount=100, basis=SHARED, page=22, reason="На всех")
    assert payout(rule, 1) == payout(rule, 4) == 100
    per = RewardRule(id="per-hero", amount=50, basis=PER_CHARACTER, page=10, reason="Каждому")
    assert payout(per, 4) == 50


def test_an_unpriced_reward_awaits_adaptation_instead_of_paying_zero():
    rule = RewardRule(id="eggs", kind="experience", amount=None, page=31,
                      reason='Яйца оцениваются в серебре, не в опыте')
    assert rule.pending_adaptation is True
    with pytest.raises(ValueError):
        payout(rule, 4)
    progression = Progression(mode=MILESTONE, milestones=[Milestone(level=2, episode="e1", page=15)],
                              rewards=[rule])
    assert [r.id for r in progression.pending] == ["eggs"]
    assert [p["id"] for p in progression.public()["pending"]] == ["eggs"]


def test_milestone_mode_without_a_milestone_is_rejected():
    with pytest.raises(ValueError):
        Progression(mode=MILESTONE)


def test_an_unknown_scenario_has_not_chosen_a_model_yet():
    assert Progression().mode == UNSET
    assert for_definition("nonsense").mode == UNSET


def test_experience_mode_accepts_rewards_without_milestones():
    progression = for_definition(EXPERIENCE, rewards=[dict(id="a", amount=50, page=10, reason="по 50 ХР")])
    assert progression.mode == EXPERIENCE
    assert progression.milestones == []


def test_a_milestone_without_a_page_cannot_be_built():
    with pytest.raises(Exception):
        Milestone(level=3, episode="x")


def test_a_non_experience_reward_carries_no_experience():
    with pytest.raises(ValueError):
        RewardRule(id="item", kind="item", amount=50, page=10, reason="предмет не в опыте")
