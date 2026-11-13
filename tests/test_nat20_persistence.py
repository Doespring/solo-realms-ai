"""Приёмка §9: персонаж переживает сохранение, повреждение отвергается."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader

LOADER = BundledAssetLoader()


def _scores() -> AbilityScores:
    return AbilityScores(
        strength=16, dexterity=14, constitution=14,
        intelligence=16, wisdom=14, charisma=14,
    )


@pytest.mark.parametrize(
    'classes,picks',
    [
        ({'wizard': 3}, ('cantrip:fire-bolt', 'spell:magic-missile')),
        ({'fighter': 5, 'wizard': 3}, ('cantrip:fire-bolt',)),
        ({'warlock': 3, 'sorcerer': 2}, ()),
        ({'cleric': 17}, ()),
    ],
)
def test_a_saved_sheet_reloads_to_the_same_derived_state(
    classes: dict[str, int], picks: tuple[str, ...]
) -> None:
    """§9: сохранение и повторная загрузка дают ТОТ ЖЕ лист, до уровня 17."""
    spec = CharacterBuildSpec(
        species_slug='human', background_slug='soldier',
        classes=classes, ability_scores=_scores(), selected_choices=picks,
    )
    first = derive_sheet(spec, loader=LOADER)
    restored = CharacterBuildSpec.model_validate_json(spec.model_dump_json())
    second = derive_sheet(restored, loader=LOADER)
    assert first.model_dump() == second.model_dump()


def test_a_saved_sheet_keeps_its_slots_and_features_across_reload() -> None:
    """Не просто «не падает»: слоты и признаки совпадают поимённо."""
    spec = CharacterBuildSpec(
        species_slug='human', background_slug='soldier',
        classes={'fighter': 5, 'wizard': 3},
        ability_scores=_scores(), selected_choices=('cantrip:fire-bolt',),
    )
    first = derive_sheet(spec, loader=LOADER)
    second = derive_sheet(
        CharacterBuildSpec.model_validate_json(spec.model_dump_json()), loader=LOADER,
    )
    assert dict(first.spell_slots) == dict(second.spell_slots)
    assert tuple(first.features) == tuple(second.features)
    assert dict(first.spell_abilities) == dict(second.spell_abilities)


@pytest.mark.parametrize(
    'payload',
    ['{}', '{"classes": {"wizard": 999}}', 'not json'],
)
def test_a_corrupt_save_is_refused_instead_of_yielding_a_plausible_sheet(
    payload: str,
) -> None:
    """§10: повреждённое сохранение даёт отказ, а не правдоподобный лист."""
    with pytest.raises(ValidationError):
        CharacterBuildSpec.model_validate_json(payload)


def test_reloading_keeps_the_metamagic_the_character_chose() -> None:
    """§5/§9: выбор переживает сохранение через `selected_choices`.

    `chosen_features` — ПРОИЗВОДНОЕ листа (вход — `selected_choices`), поэтому
    проверяется и лист, и доехавший до сохранения выбор.
    """
    spec = CharacterBuildSpec(
        species_slug='human', class_slug='sorcerer', background_slug='soldier',
        level=5, subclass_slug='draconic',
        ability_scores=AbilityScores(
            strength=8, dexterity=14, constitution=14,
            intelligence=10, wisdom=12, charisma=17,
        ),
        selected_choices=('careful-spell', 'quickened-spell'),
    )
    first = derive_sheet(spec, loader=LOADER)
    assert {'careful-spell', 'quickened-spell'} <= set(first.chosen_features)
    second = derive_sheet(
        CharacterBuildSpec.model_validate_json(spec.model_dump_json()), loader=LOADER,
    )
    assert set(second.chosen_features) == set(first.chosen_features)
    # и сам выбор действительно доехал до сохранения
    payload = json.loads(spec.model_dump_json())
    assert set(payload['selected_choices']) == {'careful-spell', 'quickened-spell'}
    restored = CharacterBuildSpec.model_validate_json(spec.model_dump_json())
    assert restored.selected_choices == spec.selected_choices
