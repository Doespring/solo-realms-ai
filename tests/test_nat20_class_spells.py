"""Требование 5: заклинания класса доходят до листа и подчиняются квоте.

Источник — `canonical/classes/wizard.json`:
```
spellcasting: {"ability": "int", "progression": "full"}
ScaleValue "Max Prepared Spells": scale {"1": 4, "10": 15, ...}
ScaleValue "Cantrips Known":       scale {"1": 3, "4": 4, "10": 5}
```

Дефект найден отрицательным примером: волшебник 1 уровня, БЕЗ выбора,
получает пустые списки, а в листе нет ПОЛЕЙ заклинаний вовсе —
`DerivedSheet.model_fields` содержит только `spell_abilities` и `spell_slots`.
То есть заговоры и подготовленные заклинания не представлены в листе,
а их квоты из корпуса никем не проверяются.
"""
import pytest

from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, DerivedSheet, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=8, dexterity=12, constitution=14,
    intelligence=16, wisdom=10, charisma=10,
)


def _wizard(level=1):
    return CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="sage",
        level=level, ability_scores=SCORES,
    )


def test_the_sheet_carries_what_the_character_can_cast():
    """Лист обязан нести заговоры и подготовленные заклинания, а не только ячейки."""
    sheet = derive_sheet(_wizard(), loader=BundledAssetLoader())
    fields = set(DerivedSheet.model_fields)
    assert {"cantrips", "prepared_spells"} <= fields, (
        "в листе нет полей заклинаний: " + str(sorted(f for f in fields if "spell" in f))
    )
    assert hasattr(sheet, "cantrips") and hasattr(sheet, "prepared_spells")


def test_the_corpus_cantrip_quota_is_reachable():
    """Волшебник 1: `Cantrips Known` = 3 — значение должно быть доступно движку."""
    from dnd5e_srd_data.loader import BundledAssetLoader as Loader

    wizard = Loader().get_class("wizard")
    scales = {
        str(getattr(a, "title", "")): a
        for a in (wizard.advancement or ())
        if "ScaleValue" in str(getattr(a, "type", ""))
    }
    assert any("Cantrips" in name for name in scales), sorted(scales)
    assert any("Prepared" in name for name in scales), sorted(scales)

C3 = ("cantrip:fire-bolt", "cantrip:light", "cantrip:mage-hand")
P4 = ("spell:magic-missile", "spell:shield", "spell:detect-magic", "spell:burning-hands")


def _final(picks):
    return CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="sage",
        level=1, ability_scores=SCORES,
        selected_choices=("skilled",) + picks, require_complete_choices=True,
    )


def test_the_corpus_quota_reaches_the_sheet():
    """Волшебник 1: 3 заговора, 4 подготовленных — ровно столько и в листе."""
    sheet = derive_sheet(_final(C3 + P4), loader=BundledAssetLoader())
    assert len(sheet.cantrips) == 3, sheet.cantrips
    assert len(sheet.prepared_spells) == 4, sheet.prepared_spells
    # Начиная с раунда 85 лист хранит СЛАГИ: токен `cantrip:x@class` —
    # запись выбора, а не имя заклинания, и он не годится ни в
    # `spells_known`, ни в `spell_abilities`. Источник выбора не потерян:
    # `selected_choices` сохраняет токены как есть (требование 3), а квоты
    # сверяются по ним в `_class_spells`.
    assert set(sheet.cantrips) == {"fire-bolt", "light", "mage-hand"}, sheet.cantrips


def test_a_cantrip_over_the_corpus_quota_is_refused():
    """`Cantrips Known` = 3; четвёртый заговор — отказ, а не молчаливое обрезание."""
    with pytest.raises(ValueError) as caught:
        derive_sheet(
            _final(C3 + ("cantrip:ray-of-frost",) + P4), loader=BundledAssetLoader()
        )
    assert "заговор" in str(caught.value), str(caught.value)


def test_a_final_sheet_short_of_its_cantrips_is_refused():
    """Завершённый лист без всех заговоров — отказ с названием квоты."""
    with pytest.raises(ValueError) as caught:
        derive_sheet(_final(C3[:2] + P4), loader=BundledAssetLoader())
    text = str(caught.value)
    assert "заговор" in text and "3" in text, text


def test_a_non_caster_gets_no_spell_quota():
    """Боец не заклинает — никаких заговоров и подготовок."""
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="soldier",
        level=1, ability_scores=SCORES,
    )
    sheet = derive_sheet(spec, loader=BundledAssetLoader())
    assert sheet.cantrips == ()
    assert sheet.prepared_spells == ()
