"""Приёмка §5: мультикласс — слоты, признаки и владелец заклинаний."""

from __future__ import annotations

import pytest

from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader

LOADER = BundledAssetLoader()


def _scores(**over: int) -> AbilityScores:
    base = dict(
        strength=10, dexterity=14, constitution=14,
        intelligence=16, wisdom=16, charisma=14,
    )
    base.update(over)
    return AbilityScores(**base)


def _sheet(classes: dict[str, int], picks: tuple[str, ...] = (), **over: int) -> object:
    spec = CharacterBuildSpec(
        species_slug='human', background_slug='soldier',
        classes=classes, ability_scores=_scores(**over), selected_choices=picks,
    )
    return derive_sheet(spec, loader=LOADER)


def test_multiclass_slots_follow_the_combined_caster_table() -> None:
    """Волшебник 3 / жрец 2 — заклинатель 5-го уровня: {1:4, 2:3, 3:2}."""
    sheet = _sheet({'wizard': 3, 'cleric': 2})
    assert dict(sheet.spell_slots) == {1: 4, 2: 3, 3: 2}


def test_a_half_caster_contributes_half_its_levels() -> None:
    """Воин 5 не заклинает; с волшебником 3 слоты остаются волшебничьими."""
    assert dict(_sheet({'wizard': 3, 'fighter': 2}).spell_slots) == {1: 4, 2: 2}


def test_warlock_pact_slots_stay_separate_from_regular_slots() -> None:
    """Колдун 3 / чародей 2: обычные {1:3}, договорные {2:2}."""
    sheet = _sheet({'warlock': 3, 'sorcerer': 2})
    assert dict(sheet.spell_slots) == {1: 3}
    assert dict(sheet.pact_slots) == {2: 2}


def test_an_illegal_multiclass_is_refused_with_the_missing_ability() -> None:
    """§5: отказ объясним — названа характеристика, которой не хватает."""
    with pytest.raises(ValueError) as caught:
        _sheet({'wizard': 3, 'paladin': 2}, strength=10)
    message = str(caught.value)
    assert 'paladin' in message and 'strength' in message


def test_features_of_both_classes_reach_the_sheet() -> None:
    """Волшебник 3 / воин 5 несёт признаки и воина, и волшебника."""
    features = set(_sheet({'fighter': 5, 'wizard': 3}).features)
    assert {'second-wind', 'action-surge', 'extra-attack'} <= features
    assert {'arcane-recovery', 'spellcasting'} <= features


def test_spell_ability_follows_the_class_taken_first() -> None:
    """§5: без `@<class>` заклинание принадлежит первому заклинающему классу."""
    picks = (
        'cantrip:fire-bolt', 'spell:magic-missile', 'spell:shield',
        'cantrip:sacred-flame', 'spell:cure-wounds',
    )
    wizard_first = _sheet({'wizard': 3, 'cleric': 2}, picks)
    assert wizard_first.spell_abilities['magic-missile'] == 'int'
    assert wizard_first.spell_abilities['sacred-flame'] == 'int'
    cleric_first = _sheet({'cleric': 2, 'wizard': 3}, picks)
    assert cleric_first.spell_abilities['magic-missile'] == 'wis'
    assert cleric_first.spell_abilities['sacred-flame'] == 'wis'


def test_an_explicit_owner_token_beats_the_class_order() -> None:
    """`@cleric` на заклинании жреца даёт мудрость даже у волшебника-первого."""
    picks = (
        'cantrip:fire-bolt@wizard', 'spell:magic-missile@wizard',
        'cantrip:sacred-flame@cleric', 'spell:cure-wounds@cleric',
    )
    abilities = _sheet({'wizard': 3, 'cleric': 2}, picks).spell_abilities
    assert abilities['fire-bolt'] == 'int'
    assert abilities['sacred-flame'] == 'wis'
    assert abilities['cure-wounds'] == 'wis'

def test_an_unsuffixed_cantrip_is_charged_to_the_class_taken_first() -> None:
    """Квота и владелец обязаны согласоваться: обе берут ПЕРВЫЙ класс сборки.

    Паладин не даёт заговоров вообще, волшебник даёт. Значит взятый без
    `@<class>` заговор у паладин-первого — незаконный (квота 0), а у
    волшебник-первого — законный и получает интеллект.

    До правки квота шла по `sorted(quotas)`: `cleric` вставал раньше
    `wizard`, а `paladin` совпадал с порядком сборки — поэтому дефект был
    виден только на сборке, где алфавит расходится с порядком И квоты
    различаются.
    """
    with pytest.raises(ValueError) as caught:
        _sheet({'paladin': 2, 'wizard': 3}, ('cantrip:fire-bolt',), strength=16)
    assert 'paladin' in str(caught.value) and '0' in str(caught.value)
    sheet = _sheet({'wizard': 3, 'paladin': 2}, ('cantrip:fire-bolt',), strength=16)
    assert sheet.spell_abilities == {'fire-bolt': 'int'}

