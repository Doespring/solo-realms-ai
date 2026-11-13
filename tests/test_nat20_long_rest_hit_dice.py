"""A Long Rest restores the Hit Dice pool (SRD 5.2: "You also regain all your
expended Hit Dice"), and the engine's rest resolver is the one the host calls.

The engine models the pool as ``{die face: count remaining}``; ``Combatant``
carries ``hit_dice_remaining``/``hit_dice_total`` and a combat persists the
spent count in ``custom_counters['hit_dice_spent']``. The rest itself lives in
``dnd5e_engine.rest`` (``HitDicePool``, ``resolve_short_rest``,
``resolve_long_rest``) — the pure resolver the host worker drives. This file
pins the behaviour a rest must produce on THAT pool, and that a Short Rest
does not silently refill it.
"""

from __future__ import annotations

import random

from dnd5e_engine.rest import HitDicePool, resolve_long_rest, resolve_short_rest


def test_a_long_rest_restores_every_spent_hit_die() -> None:
    pool = HitDicePool(hit_die_size=10, dice_remaining=1, dice_total=5)
    outcome = resolve_long_rest(pool, hp_current=3, hp_max=44)
    assert outcome.pool.dice_remaining == 5
    assert outcome.dice_remaining == 5
    assert outcome.hp_current == 44


def test_a_long_rest_of_an_unspent_pool_changes_nothing() -> None:
    pool = HitDicePool(hit_die_size=10, dice_remaining=5, dice_total=5)
    outcome = resolve_long_rest(pool, hp_current=44, hp_max=44)
    assert outcome.pool.dice_remaining == 5
    assert outcome.healed == 0


def test_a_short_rest_spends_dice_and_never_refills_them() -> None:
    pool = HitDicePool(hit_die_size=10, dice_remaining=3, dice_total=5)
    outcome = resolve_short_rest(pool, 2, 3, rng=random.Random(1))
    assert outcome.dice_spent == 2
    assert outcome.pool.dice_remaining == 1
    assert outcome.dice_remaining == 1
    assert outcome.healed == sum(outcome.rolls)


def test_an_overspend_is_refused_not_clamped() -> None:
    pool = HitDicePool(hit_die_size=10, dice_remaining=1, dice_total=5)
    try:
        resolve_short_rest(pool, 2, 3, rng=random.Random(1))
    except ValueError as refused:
        assert 'Hit Dice' in str(refused)
    else:  # pragma: no cover - the refusal is the point
        raise AssertionError('an overspend must raise, never clamp')


def test_the_rest_resolver_draws_nothing_for_a_long_rest() -> None:
    """A Long Rest is unconditional recovery: seeded replays stay identical."""
    pool = HitDicePool(hit_die_size=8, dice_remaining=0, dice_total=4)
    first = resolve_long_rest(pool, hp_current=0, hp_max=30)
    second = resolve_long_rest(pool, hp_current=0, hp_max=30)
    assert first.rolls == second.rolls == ()
    assert first.pool == second.pool