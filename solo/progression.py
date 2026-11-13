"""Advancement is a property of the scenario, not of the combat log.

The book states both models and lets the group choose (page 7: milestone
"instead of the usual rules"): an episode ends, a level arrives. Other
scenarios count experience instead. Which one a prepared adventure uses is a
preparation choice, recorded in the definition and carried into the save - it
is never inferred from whatever the fight happened to award.

Nat20 owns the rules arithmetic (advance, validate_choices, derive_sheet,
level_for_experience). This module owns only the host policies Nat20 does not
state: which model the scenario chose, which milestone levels its book fixes,
and how a printed reward turns into one ledger entry applied once.
"""
from __future__ import annotations
from typing import Literal
from pydantic import Field, model_validator
from .contracts import Strict

MILESTONE = 'milestone'
EXPERIENCE = 'experience'
# A definition that came from nowhere in particular has not chosen a model
# yet. Saying so is honest; silently defaulting to one of the two would let an
# unprepared adventure look like a decision the book made.
UNSET = 'unset'
MODES = (MILESTONE, EXPERIENCE, UNSET)

# A printed reward is paid on one of two bases, and the book uses both in the
# same episode: "по 100 ХР каждый" is per character, "25 ХР за каждого
# спасённого на всех" is shared by the group.
PER_CHARACTER = 'per_character'
SHARED = 'shared'

# What earned the reward. The book pays for more than fighting (page 10: NPCs
# brought in alive; page 42: an event resolved; page 31: a trap disarmed).
SOURCES = ('combat', 'social', 'exploration', 'story')
BASES = (PER_CHARACTER, SHARED)


class Milestone(Strict):
    """One level the book grants at the end of an episode.

    page is the printed page that says so; a milestone without a page would be
    an invention and is rejected.
    """

    level: int = Field(ge=2, le=20)
    episode: str = Field(min_length=1, max_length=80)
    page: int = Field(ge=1, le=2000)

    @model_validator(mode='after')
    def named_episode(self):
        if not self.episode.strip():
            raise ValueError('Milestone needs the episode it closes')
        return self


class RewardRule(Strict):
    """A printed reward, kept as data with its page and its payout base.

    amount is None when the book grants the reward but its value is not
    supported by the current rules data - the honest state the prompt requires
    instead of booking a zero.
    """

    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,79}$')
    kind: Literal['experience', 'item', 'story'] = 'experience'
    amount: int | None = Field(default=None, ge=0, le=1000000)
    basis: Literal['per_character', 'shared'] = PER_CHARACTER
    earned_by: Literal['combat', 'social', 'exploration', 'story'] = 'story'
    page: int = Field(ge=1, le=2000)
    reason: str = Field(min_length=3, max_length=400)

    @model_validator(mode='after')
    def coherent(self):
        if self.kind != 'experience' and self.amount is not None:
            raise ValueError('A non-experience reward carries no experience amount')
        return self

    @property
    def pending_adaptation(self) -> bool:
        return self.kind == 'experience' and self.amount is None


class Progression(Strict):
    """The scenario advancement model, with the evidence that selected it."""

    mode: Literal['milestone', 'experience', 'unset'] = UNSET
    milestones: list[Milestone] = Field(default_factory=list, max_length=40)
    rewards: list[RewardRule] = Field(default_factory=list, max_length=200)
    note: str = Field(default='', max_length=800)

    @model_validator(mode='after')
    def one_id_once(self):
        if len({m.level for m in self.milestones}) != len(self.milestones):
            raise ValueError('Повтор уровня в рубежах развития')
        if len({r.id for r in self.rewards}) != len(self.rewards):
            raise ValueError('Повтор награды')
        if self.mode == MILESTONE and not self.milestones:
            raise ValueError('Milestone mode needs at least one milestone')
        return self

    def level_at(self, episode: str) -> int | None:
        """The level this episode grants, if the book fixes one."""
        rows = [m.level for m in self.milestones if m.episode == episode]
        return max(rows) if rows else None

    @property
    def pending(self) -> list[RewardRule]:
        return [r for r in self.rewards if r.pending_adaptation]

    @property
    def supported(self) -> list[RewardRule]:
        return [r for r in self.rewards if not r.pending_adaptation]

    def public(self) -> dict:
        """What the player is told: the model, and what awaits adaptation."""
        return dict(mode=self.mode,
                    milestones=[m.model_dump() for m in self.milestones],
                    pending=[dict(id=r.id, page=r.page, reason=r.reason) for r in self.pending],
                    note=self.note)


def payout(rule: RewardRule, party_size: int) -> int:
    """The ledger amount for one reward, split so the party stays one level.

    Experience is pooled: every character advances together, so a
    per-character line pays amount once into the pool and a shared line pays
    amount once for the group. Shrinking the pool by party size would starve a
    larger party of the level its book grants, and paying each member the full
    pool would multiply it; the pool is the party, not one hero.
    """
    if rule.pending_adaptation:
        raise ValueError('Награда ждёт адаптации: ' + rule.id + '. Начислять ноль нельзя.')
    if rule.kind != 'experience':
        return 0
    if party_size < 1:
        raise ValueError('Размер партии должен быть положительным')
    return int(rule.amount)


def shared_across(rule: RewardRule, characters: int) -> int:
    """What each character of the party receives from one shared reward.

    Page 10 is explicit: the experience for NPCs brought in alive "следует
    поделить между персонажами поровну". The pool therefore shrinks as the
    party grows, which is exactly what keeps a larger party from levelling
    faster than a smaller one - the party advances together or not at all.
    """
    total = payout(rule, characters)
    if rule.basis == SHARED:
        return total // characters
    return total


def party_total(rules, characters: int) -> int:
    """The experience pool the whole party earned from these rewards."""
    total = 0
    for rule in rules:
        total += payout(rule, characters)
    return total


def book_progression() -> Progression:
    """The progression the book prints for itself.

    Milestones come from the episode closings (pages 15, 22, 31, 42); page 7
    adds that the fifth episode grants no level ("кроме 5го"). Rewards are the
    printed experience lines with their pages. Every entry is a quotation.
    """
    milestones = [Milestone(level=2, episode='hoard-episode-1', page=15),
                  Milestone(level=3, episode='hoard-episode-2', page=22),
                  Milestone(level=4, episode='hoard-episode-3', page=31),
                  Milestone(level=5, episode='hoard-episode-4', page=42)]
    rewards = [
        RewardRule(id='escort-npc-alive', amount=50, basis=PER_CHARACTER, earned_by='social', page=10,
                   reason='По 50 Очков Опыта за каждого НИП, доставленного живым в крепость; опыт делится поровну.'),
        RewardRule(id='drive-off-dragon', amount=50, basis=PER_CHARACTER, earned_by='story', page=12,
                   reason='По 50 ХР за то, что отогнали дракона; 25 ХР, если погибло больше 10 защитников.'),
        RewardRule(id='mill-ambush-uncovered', amount=50, basis=PER_CHARACTER, earned_by='exploration', page=13,
                   reason='50 ХР, если персонажи поняли, что поджог — спектакль; 25 ХР при позднем раскрытии.'),
        RewardRule(id='temple-evacuation', amount=100, basis=PER_CHARACTER, earned_by='social', page=14,
                   reason='По 100 ХР каждому за спасение людей из храма; 50 ХР, если погибло больше 10 горожан.'),
        RewardRule(id='champion-duel', amount=50, basis=PER_CHARACTER, earned_by='story', page=15,
                   reason='По 50 ХР всем, если кто-то вышел на бой с чемпионом.'),
        RewardRule(id='camp-infiltration', amount=100, basis=PER_CHARACTER, earned_by='exploration', page=22,
                   reason='По 100 ХР каждому за проникновение в лагерь.'),
        RewardRule(id='camp-unseen-exit', amount=100, basis=PER_CHARACTER, earned_by='exploration', page=22,
                   reason='По 100 ХР за проникновение и уход без тревоги.'),
        RewardRule(id='rescue-leosin', amount=250, basis=PER_CHARACTER, earned_by='social', page=22,
                   reason='По 250 ХР каждому за спасение Леосина.'),
        RewardRule(id='rescue-other-prisoners', amount=25, basis=SHARED, earned_by='social', page=22,
                   reason='По 25 ХР за каждого живого спасённого, на всех.'),
        RewardRule(id='camp-exploration-point', amount=25, basis=PER_CHARACTER, earned_by='exploration', page=22,
                   reason='По 25 ХР за каждый самостоятельно найденный пункт исследования лагеря.'),
        RewardRule(id='disarmed-trap', amount=100, basis=SHARED, earned_by='exploration', page=31,
                   reason='100 ХР за каждую обнаруженную и обезвреженную ловушку, на всех.'),
        RewardRule(id='road-event-resolved', amount=300, basis=PER_CHARACTER, earned_by='story', page=42,
                   reason='300 ХР каждому персонажу за успешное разрешение происшествия.'),
    ]
    return Progression(mode=MILESTONE, milestones=milestones, rewards=rewards,
                       note='Книга предлагает Поэтапное Получение Уровней вместо обычных правил '
                            '(стр. 7); уровни даются по окончании эпизодов, кроме пятого.')


def for_definition(mode: str, milestones=(), rewards=()) -> Progression:
    """The progression a prepared adventure carries, validated by its mode."""
    rows = [Milestone.model_validate(m) if isinstance(m, dict) else m for m in milestones]
    paid = [RewardRule.model_validate(r) if isinstance(r, dict) else r for r in rewards]
    return Progression(mode=mode if mode in MODES else UNSET, milestones=rows, rewards=paid)


__all__ = ['MILESTONE', 'EXPERIENCE', 'MODES', 'PER_CHARACTER', 'SHARED', 'BASES', 'SOURCES',
           'UNSET', 'Milestone', 'RewardRule', 'Progression', 'payout', 'shared_across', 'party_total',
           'book_progression', 'for_definition']
