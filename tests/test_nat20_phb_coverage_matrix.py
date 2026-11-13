"""Матрица покрытия PHB 2024 — исполняемый реестр обязательств.

Требование 4 задаёт матрицу как СПИСОК ОБЯЗАТЕЛЬСТВ с отметками:
«реализовано и проверено», «реализовано частично», «отсутствует»,
«требует решения Мастера». Требование 14.1 требует, чтобы разделы были
сопоставлены с реализацией, а 14.4 — чтобы не было известных обязательных
заглушек, скрытых за формулировками.

Этот файл — не документ, а реестр, который падает, если действительность
разойдётся с заявленным статусом. Статус каждой строки проверяется числом из
корпуса; строки со статусом PARTIAL или ABSENT перечислены явно и их нельзя
перевести в DONE, не изменив проверку осознанно.

Источник: SRD 5.2 (2024). Отпечаток корпуса закреплён в vendor/nat20/CORPUS.json
и проверяется tests/test_nat20_corpus_integrity.py.
"""

from dnd5e_engine.rules.advancement import species_lineages, subclass_gate, subclass_options
from dnd5e_srd_data.loader import BundledAssetLoader
from solo.nat20_prototype import spell_execution_status

import pytest


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def test_species_and_their_variants(loader):
    """Раздел «Виды»: все виды и линейки присутствуют и выбираемы."""
    lineages = species_lineages(loader)
    assert len(lineages) == 9, "девять базовых видов SRD 5.2"
    assert sum(len(x["variants"]) for x in lineages) == 14
    requiring = [x["base"] for x in lineages if x["lineage_required"]]
    assert sorted(requiring) == ["Elf", "Gnome", "Tiefling"], requiring


def test_backgrounds(loader):
    """Признаётся ограничение корпуса: SRD публикует 4 предыстории."""
    assert len(loader.list_slugs("backgrounds")) == 4


def test_all_twelve_classes_gate_their_subclass_at_three(loader):
    assert sorted(loader.list_slugs("classes")) == sorted([
        "barbarian", "bard", "cleric", "druid", "fighter", "monk",
        "paladin", "ranger", "rogue", "sorcerer", "warlock", "wizard",
    ])
    for slug in loader.list_slugs("classes"):
        assert subclass_gate(slug, loader=loader) == 3, slug
        assert len(subclass_options(slug, 20, loader=loader)) >= 1, slug


def test_SUBCLASSES_ARE_A_KNOWN_DATA_GAP(loader):
    """ЧАСТИЧНО: по одному подклассу на класс вместо ~4 в полном PHB.

    Механика подклассов работает (порог, выбор, способности по уровням),
    но корпус содержит 12 подклассов, а не полный набор книги. Это пробел
    ДАННЫХ, а не обработчика: разбор, выбор и применение способностей
    реализованы и проверены. Названо явно, чтобы не выдавать за полное
    покрытие в соответствии с требованием 14.
    """
    total = sum(
        len(subclass_options(slug, 20, loader=loader))
        for slug in loader.list_slugs("classes")
    )
    assert total == 12, "по одному на класс; расширение меняет эту строку осознанно"


def test_feats_are_present(loader):
    assert len(loader.list_slugs("feats")) == 17


def test_spells_split_into_executable_and_declared_unsupported(loader):
    """Раздел «Заклинания»: различаем описание и исполняемую механику."""
    statuses = {}
    for slug in loader.list_slugs("spells"):
        raw = loader.get_spell(slug).model_dump(mode="json")
        statuses[slug] = spell_execution_status(raw)
    native = sum(1 for s in statuses.values() if s == "native_candidate")
    # 223 с раунда 76 (Shillelagh), 222 до него. Доли пересчитаны по корпусу
    # и проверены в tests/test_nat20_spell_coverage.py, где рост разобран
    # поимённо; здесь важно, что обе доли сходятся в 339.
    assert native == 223
    assert sum(1 for s in statuses.values() if s != "native_candidate") == 116

def test_conditions_are_complete(loader):
    """Все 15 условий SRD 5.2 присутствуют; механика — в коде правил."""
    assert len(loader.list_slugs("conditions")) == 15


def test_items_and_equipment_economy(loader):
    assert len(loader.list_slugs("items")) == 546


def test_features_and_traits_are_present(loader):
    assert len(loader.list_slugs("features")) == 261
    assert len(loader.list_slugs("traits")) == 102


def test_statblocks_arrive_through_a_contract(loader):
    """Контракт статблоков: 341 существо принимается, бестиарий не переносится."""
    assert len(loader.list_slugs("monsters")) == 341


#: Разделы, которые НЕ объявлены завершёнными, с точной причиной.
KNOWN_GAPS = {
    "subclasses_full_phb": "корпус содержит 12 подклассов (по одному на класс); полный PHB даёт ~4 на класс — пробел данных",
    "backgrounds_full_phb": "корпус SRD содержит 4 предыстории; полный PHB содержит больше — пробел данных",
    "hybrid_rules_data": "каталог rules_data отсутствует в репозитории; 89 гибридных тестов падают, Bless недоступен",
    "campaign_spell_choice": "solo/game_commands.py закрепляет spell_id Literal[bless] — правка в зоне интегратора",
    "mixed_class_rest": "solo/nat20_worker.py:297 отвергает отдых при мультиклассе — правка в зоне интегратора",
}


def test_every_gap_is_named_with_a_reason():
    """Требование 14: не прятать незавершённый раздел за формулировкой."""
    for key, reason in KNOWN_GAPS.items():
        assert reason and len(reason) > 20, key
    assert len(KNOWN_GAPS) == 5

