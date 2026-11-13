"""Требование 11: получение доступных вариантов — часть публичного контракта.

Найденный пробел: в `dnd5e_engine.__all__` (64 имени) не было НИ ОДНОЙ функции
про варианты выбора, хотя п.11 требует «получения доступных вариантов» и
разделения проверки и исполнения. Список строился приватной `_choice_options`,
недостижимой для хоста.

Здесь проверяется `available_choices`: что она отдаёт, что следует за уровнем и —
главное — что просмотр ничего не тратит и не трогает случайность.
"""
import random

from dnd5e_engine import available_choices, derive_sheet
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec
from dnd5e_srd_data.loader import BundledAssetLoader


SCORES = AbilityScores(
    strength=16, dexterity=14, constitution=14,
    intelligence=10, wisdom=10, charisma=10,
)


def _fighter(level):
    return CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="soldier",
        level=level, ability_scores=SCORES,
    )


def test_a_fighter_level_one_offers_the_fighting_styles():
    """SRD 5.2 Fighter: a Fighting Style is chosen at level 1."""
    options = available_choices(_fighter(1), loader=BundledAssetLoader())
    assert "archery" in options, options
    assert "defense" in options
    assert options["archery"] == "feat"


def test_the_option_list_is_public_api():
    """Без экспорта хост не может показать список (п.11)."""
    import dnd5e_engine as E
    assert "available_choices" in E.__all__


def test_asking_for_options_spends_nothing():
    """Просмотр вариантов не расходует ресурсы и не меняет случайность (п.11)."""
    spec = _fighter(1)
    before = spec.model_dump()
    state = random.getstate()
    available_choices(spec, loader=BundledAssetLoader())
    assert spec.model_dump() == before, "просмотр изменил описание персонажа"
    assert random.getstate() == state, "просмотр трогал глобальную случайность"


def test_the_offered_options_are_exactly_the_ones_accepted():
    """Список показанный и список проверяемый — один и тот же (иначе выбор
    выглядит законным, а потом отвергается)."""
    spec = _fighter(1)
    options = available_choices(spec, loader=BundledAssetLoader())
    assert options, "боец 1 уровня обязан иметь варианты"
    # Стиль боя — обычный пул: выбирается ГОЛЫМ СЛАГОМ, без префикса.
    # `feat:`/`asi:` зарезервированы за повышением характеристик на уровнях ASI
    # (`rules/choices.py:164-181`).
    # Пул включает и предысторию (soldier даёт savage-attacker), поэтому берём
    # только стили боя — их предлагает именно классовое feature_choice.
    styles = {"archery", "defense", "great-weapon-fighting", "two-weapon-fighting"}
    candidates = [s for s in options if s in styles]
    assert candidates, options
    for slug in candidates:
        chosen = spec.model_copy(update={"selected_choices": (slug,)})
        sheet = derive_sheet(chosen, loader=BundledAssetLoader())  # не должно бросать
        assert slug in sheet.feats or slug in getattr(sheet, "features", ()), (
            f"вариант {slug} предложен, но в лист не попал"
        )


def test_an_unknown_class_is_refused_rather_than_returning_an_empty_list():
    """Отсутствие класса не выдаётся за «вариантов нет» (п.2)."""
    import pytest
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="necromancer", background_slug="soldier",
        level=1, ability_scores=SCORES,
    )
    with pytest.raises(ValueError):
        available_choices(spec, loader=BundledAssetLoader())

def test_a_wizard_is_never_offered_a_fighting_style():
    """Пул открывается уровнем ИМЕННО этого класса, а не общим списком корпуса.

    Мутация снятия гейта по уровню в `_choice_options` (`if any(step.count > 0
    and step.level <= level ...)` → `if True`) ловится здесь: без гейта
    волшебнику предложили бы чужие варианты.
    """
    options = available_choices(
        CharacterBuildSpec(
            species_slug="human", class_slug="wizard", background_slug="soldier",
            level=1, ability_scores=SCORES,
        ),
        loader=BundledAssetLoader(),
    )
    for style in ("archery", "defense", "great-weapon-fighting", "two-weapon-fighting"):
        assert style not in options, f"волшебнику предложен чужой стиль боя {style}"
