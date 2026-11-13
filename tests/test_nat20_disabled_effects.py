"""Выключенный эффект не должен менять расчёт боя.

Foundry хранит выключенный эффект на акторе (это и есть переключатель на листе),
но НЕ применяет его `changes`. Лист персонажа флаг учитывал, а боевые читатели —
нет: выключенный Shield всё равно давал +5 AC к атакам и спасброскам.

Здесь проверяется согласованность: `_enabled_effects` отбрасывает выключенные, и
`_fold_active_effect_changes` идёт ТОЛЬКО по включённым.
"""
from dnd5e_engine.orchestrator import _enabled_effects, _fold_active_effect_changes
from dnd5e_engine.types.effects import ActiveEffect, ActiveEffectChange, ActiveEffectDuration
from dnd5e_srd_data.loader import BundledAssetLoader


def _shield_like(disabled: bool) -> ActiveEffect:
    return ActiveEffect(
        id="effect:shield",
        name="Shield",
        origin="cast:shield:1",
        target_id="hero",
        disabled=disabled,
        changes=[ActiveEffectChange(key="system.attributes.ac.bonus", mode="add", value="5")],
        flags={},
        statuses=(),
        duration=ActiveEffectDuration(rounds=1),
    )


def test_a_disabled_effect_is_dropped_from_the_live_set():
    assert _enabled_effects([_shield_like(True)]) == []


def test_an_enabled_effect_survives():
    assert len(_enabled_effects([_shield_like(False)])) == 1


def test_a_disabled_effect_grants_no_ac_bonus():
    entry: dict = {}
    # Сырой список: фильтрация обязана произойти ВНУТРИ функции.
    _fold_active_effect_changes([_shield_like(True)], {}, entry, {})
    assert "passive_ac_bonus" not in entry


def test_an_enabled_effect_grants_its_ac_bonus():
    entry: dict = {}
    _fold_active_effect_changes([_shield_like(False)], {}, entry, {})
    assert entry["passive_ac_bonus"] == "5"


def test_the_corpus_ships_shield_disabled():
    """Данные корпуса: эффект есть, но помечен выключенным."""
    spell = BundledAssetLoader().get_spell("shield")
    effects = list(spell.passive_effects)
    assert len(effects) == 1
    assert effects[0].disabled is True
    assert effects[0].changes[0].value == "5"


def test_every_corpus_effect_with_the_flag_off_is_applied(loader=None):
    """Согласованность: эффект без флага disabled попадает в расчёт."""
    eff = _shield_like(False)
    eff = eff.model_copy(update={"disabled": None}) if False else eff
    assert len(_enabled_effects([eff])) == 1

