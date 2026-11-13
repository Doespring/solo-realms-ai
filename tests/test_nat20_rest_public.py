"""Требование 6 и 9: отдых проверяется через ПУБЛИЧНЫЙ интерфейс, не мимо него.

Найдено собственной мутацией: `resolve_long_rest` и `resolve_short_rest` сломаны
в `return` → из 40 проверок `test_nat20_consumable_rest.py` и
`test_nat20_item_rest_recovery.py` падает лишь **одна** (`test_multiclass_counts_every_class`).
Остальные бьют в низкоуровневые `recover_item_uses` / `recover_item_and_activity_pools`
и проверяют их, а не отдых.

Здесь отдых вызывается через `dnd5e_engine.rest`, как его вызывает хост.
"""
import random

from dnd5e_engine.rest import HitDicePool, resolve_long_rest, resolve_short_rest


def _pool(total=5, remaining=2):
    return HitDicePool(hit_die_size=10, dice_remaining=remaining, dice_total=total)


def test_a_long_rest_restores_all_hit_points():
    """SRD 5.2 §Long Rest: «regain all lost Hit Points». Не половину, как в 2014."""
    out = resolve_long_rest(_pool(), hp_current=7, hp_max=44)
    assert out.hp_current == 44


def test_a_long_rest_refills_the_whole_hit_die_pool():
    """2024: все кости хитов. 2014: половина. Различие — предмет проверки."""
    out = resolve_long_rest(_pool(total=5, remaining=2), hp_current=10, hp_max=44)
    assert out.pool.dice_remaining == 5, "длинный отдых 2024 возвращает ВСЕ кости"


def test_a_long_rest_restores_spell_slots():
    """«Finishing a Long Rest restores any expended spell slots.»"""
    out = resolve_long_rest(
        _pool(), hp_current=10, hp_max=44,
        spell_slots={1: 0, 2: 1}, spell_slot_max={1: 4, 2: 3},
    )
    assert out.spell_slots == {1: 4, 2: 3}


def test_a_long_rest_lowers_exhaustion_by_one_level():
    """«its level decreases by 1» — не сбрасывает в ноль."""
    out = resolve_long_rest(_pool(), hp_current=10, hp_max=44, exhaustion_level=3)
    assert out.exhaustion_level == 2


def test_a_short_rest_heals_only_what_the_dice_roll():
    """Короткий отдых не восстанавливает всё: только потраченные кости."""
    out = resolve_short_rest(_pool(remaining=3), dice_to_spend=1, con_modifier=2,
                             rng=random.Random(7))
    # Короткий отдых возвращает ПРИРАЩЕНИЕ, а не итоговые хиты:
    # `RestOutcome.healed`, а `hp_current`/`pool` остаются None (см. docstring).
    # 1d10 + 2 = 3..12, минимум 1 на кость.
    assert 3 <= out.healed <= 12
    assert out.dice_spent == 1
    assert out.dice_remaining == 2
    assert out.hp_current is None, "итоговые хиты — дело вызывающего"


def test_a_short_rest_refuses_to_spend_more_dice_than_it_has():
    """Отказ, а не тихое урезание (требование 8)."""
    import pytest
    with pytest.raises(ValueError):
        resolve_short_rest(_pool(remaining=1), dice_to_spend=2, con_modifier=0,
                           rng=random.Random(1))

