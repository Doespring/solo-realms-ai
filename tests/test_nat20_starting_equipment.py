"""Требование 5: стартовое снаряжение предыстории — из ИСТОЧНИКА, без догадок.

Ключи Foundry в `starting_equipment` (`canonical/backgrounds/*.json`) обрезаны
по 15 символов, поэтому имя предмета восстанавливается не всегда однозначно.
Проверки ниже фиксируют три вещи:

1. конкретные предметы предысторий разрешаются в РЕАЛЬНЫЕ слаги корпуса;
2. неоднозначный ключ даёт отказ, а не выбор первого попавшегося (п.2);
3. у классов записи `starting_equipment` в корпусе НЕТ — это ограничение
   источника, и движок его не выдумывает.
"""
import pytest

from dnd5e_engine.starting_equipment import (
    StartingItem,
    UnresolvedStartingItem,
    resolve_background_equipment,
    resolve_starting_key,
)


@pytest.mark.parametrize(
    "background, expected",
    [
        ("soldier", {"spear": 1, "shortbow": 1, "arrows": 20, "healers-kit": 1, "quiver": 1}),
        ("sage", {"quarterstaff": 1, "calligraphers-supplies": 1, "book": 1,
                  "parchment": 8, "robe": 1}),
        ("criminal", {"dagger": 2, "thieves-tools": 1, "crowbar": 1,
                      "pouch": 2, "clothes-travelers": 1}),
    ],
)
def test_a_background_grants_its_printed_equipment(background, expected):
    """SRD 5.2 §Starting Equipment — состав берётся из записи предыстории."""
    loadout = resolve_background_equipment(background)
    got = {item.slug: item.count for item in loadout.items}
    assert got == expected, f"{background}: {got}"


def test_every_resolved_item_exists_in_the_corpus():
    """Слаг без предмета означал бы выдуманное снаряжение."""
    from dnd5e_engine.lib_loader import get_lib_loader

    loader = get_lib_loader()
    known = set(loader.list_slugs("items"))
    for bg in ("soldier", "sage", "criminal", "acolyte"):
        for item in resolve_background_equipment(bg).items:
            assert item.slug in known, f"{bg}: {item.slug} отсутствует в корпусе"


def test_wealth_is_read_from_the_record():
    """`wealth` — число золотых монет, а не догадка движка."""
    assert resolve_background_equipment("soldier").wealth_gp == 50


def test_a_choice_entry_is_reported_not_invented():
    """`tool: game` и `focus: holy` — выбор игрока, не предмет."""
    soldier = resolve_background_equipment("soldier")
    assert "game" in soldier.unresolved
    assert "holy" in resolve_background_equipment("acolyte").unresolved


def test_an_ambiguous_key_is_refused_rather_than_guessed():
    """П.2: отсутствие однозначности — отказ, а не подстановка похожего."""
    # Неразрешимый случай — равная длина имён: правило «кратчайшее имя»
    # тоже не даёт ответа, и выбор был бы произволом.
    pair = {"ab-cd": "Ab Cd", "ab-ce": "Ab Ce"}  # `AbCd` и `AbCe` — обе 4
    with pytest.raises(UnresolvedStartingItem, match="ambiguous"):
        resolve_starting_key("Compendium.x.Item.phbwepAbC", pair)


def test_the_shorter_name_wins_when_one_prefix_extends_another():
    """`Quartersta` -> базовый `quarterstaff`, а не `quarterstaff-of-the-acrobat`."""
    catalogue = {
        "quarterstaff": "Quarterstaff",
        "quarterstaff-of-the-acrobat": "Quarterstaff of the Acrobat",
    }
    item = resolve_starting_key("Compendium.x.Item.phbwepQuartersta", catalogue)
    assert item == StartingItem(slug="quarterstaff", name="Quarterstaff", count=1)


def test_a_non_item_key_resolves_to_nothing():
    """`game` — не предмет; это должно быть видно, а не превращаться в предмет."""
    assert resolve_starting_key("game", {"spear": "Spear"}) is None


def test_classes_carry_no_starting_equipment_in_this_corpus():
    """Ограничение ИСТОЧНИКА: у классов только `wealth`, без списка предметов.

    Проверка фиксирует факт, чтобы «пустой инвентарь» не спутали с дефектом
    движка: разрешать нечего, потому что записей нет.
    """
    from dnd5e_engine.lib_loader import get_lib_loader

    loader = get_lib_loader()
    without = [s for s in loader.list_slugs("classes")
               if not getattr(loader.get_class(s), "starting_equipment", None)]
    assert len(without) == len(loader.list_slugs("classes"))


def test_the_sheet_carries_the_background_equipment():
    """Требование 5 и 11: снаряжение видно через ПУБЛИЧНЫЙ лист, не только модулем.

    До этой проверки `starting_equipment` жил отдельным модулем: лист персонажа
    его не содержал, и «предмет есть в источнике» не означало «предмет есть у
    персонажа» (расхождение, которое запрещает п.9).
    """
    from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
    from dnd5e_srd_data.loader import BundledAssetLoader

    scores = AbilityScores(
        strength=16, dexterity=12, constitution=14,
        intelligence=10, wisdom=12, charisma=10,
    )
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="sage",
        level=1, ability_scores=scores,
    )
    sheet = derive_sheet(spec, loader=BundledAssetLoader())
    assert ("quarterstaff", 1) in sheet.starting_equipment
    assert ("parchment", 8) in sheet.starting_equipment
    assert sheet.wealth_gp == 50


def test_a_character_without_a_background_gets_no_invented_equipment():
    """Нет предыстории — нет снаряжения: движок не подставляет «похожее» (п.2)."""
    from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
    from dnd5e_srd_data.loader import BundledAssetLoader

    scores = AbilityScores(
        strength=16, dexterity=12, constitution=14,
        intelligence=10, wisdom=12, charisma=10,
    )
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", level=1, ability_scores=scores,
    )
    sheet = derive_sheet(spec, loader=BundledAssetLoader())
    assert sheet.starting_equipment == ()
    assert sheet.wealth_gp == 0
