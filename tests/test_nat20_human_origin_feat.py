"""Требование 5: черта вида — ровно по выбору игрока, а не первая из пула.

Первая версия теста была построена на ложной посылке: я считал, что `alert`
в листе человека — автоподстановка. Факт: `alert` — черта происхождения
предыстории `criminal`, а `savage-attacker` — у `soldier` (PHB 2024: у каждой
предыстории своя черта происхождения). Выбор человека добавляется отдельно, и
две черты в листе ЗАКОННЫ.

Настоящее требование проверяет другое: какая именно из четырёх черт вида
оказалась в листе, если игрок назвал конкретную. Подстановка первой из пула
была бы скрытым исправлением выбора (п.5).
"""
import pytest

from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=16, dexterity=12, constitution=14,
    intelligence=10, wisdom=10, charisma=8,
)

SKILLS = (
    "skill:stealth", "skill:sleight-of-hand",
    "skill:acrobatics", "skill:perception",
)

# Фон выбирается так, чтобы его собственная черта не совпадала с проверяемой.
BACKGROUND_FEAT = {
    "criminal": "alert",
    "soldier": "savage-attacker",
    "sage": "magic-initiate",
    "acolyte": "magic-initiate",
}


def _human(picks=(), **kwargs):
    """Черновик листа человека; `picks` — сверх обязательных навыков плута."""
    return CharacterBuildSpec(
        species_slug="human", class_slug="rogue", background_slug="criminal",
        level=1, ability_scores=SCORES,
        selected_choices=SKILLS + tuple(picks), **kwargs
    )


def _feats(sheet):
    return [str(f) for f in (getattr(sheet, "feats", ()) or ())]


def test_the_chosen_origin_feat_is_the_one_in_the_sheet():
    """Названная игроком черта вида попадает в лист без подмены."""
    sheet = derive_sheet(
        _human(picks=("skilled",)), loader=BundledAssetLoader()
    )
    assert "skilled" in _feats(sheet), _feats(sheet)


def test_a_choice_the_player_did_not_make_is_not_added_for_them():
    """Без выбора лист не получает вторую черту вида «за игрока»."""
    sheet = derive_sheet(_human(), loader=BundledAssetLoader())
    # Единственная черта — от предыстории; черта вида не подставлена.
    assert _feats(sheet) == [BACKGROUND_FEAT["criminal"]], _feats(sheet)


def test_the_two_origin_feats_come_from_different_sources():
    """Черта предыстории и черта вида — разные источники, обе законны."""
    sheet = derive_sheet(
        _human(picks=("skilled",)), loader=BundledAssetLoader()
    )
    feats = _feats(sheet)
    assert sorted(feats) == ["alert", "skilled"], feats


def test_a_final_sheet_without_the_species_feat_is_refused():
    """Завершённый лист без выбора черты вида — отказ, а не молчание."""
    derive_sheet(_human(), loader=BundledAssetLoader())  # черновик допустим
    with pytest.raises(Exception) as caught:
        derive_sheet(
            _human(require_complete_choices=True), loader=BundledAssetLoader()
        )
    text = str(caught.value).lower()
    assert "human" in text or "choice" in text, text

