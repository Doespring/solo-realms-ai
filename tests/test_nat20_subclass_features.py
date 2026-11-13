"""Критерий 14.2 для подклассов: способности выдаются на своих уровнях.

Классовые способности проверены в `test_nat20_class_features.py`. Здесь — то,
что оставалось незакрытым: подклассовые.

Ограничение корпуса названо прямо: SRD 5.2 публикует по ОДНОМУ подклассу на класс
(12 всего), а полная книга даёт около четырёх. Это пробел данных, а не механики,
и он закреплён отдельной проверкой, чтобы не выдавать частичное покрытие за полное.
"""
import pytest

from dnd5e_srd_data.loader import BundledAssetLoader


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def _levels(loader, slug):
    doc = loader.get_subclass(slug)
    return {g.slug: g.level for g in doc.granted_features}


# SRD 5.2 Fighter — Champion.
CHAMPION = {
    "improved-critical": 3,
    "remarkable-athlete": 3,
    "additional-fighting-style": 7,
    "heroic-warrior": 10,
    "superior-critical": 15,
    "survivor": 18,
}


def test_champion_features_land_on_their_levels(loader):
    assert _levels(loader, "champion") == CHAMPION


# SRD 5.2 Wizard — Evoker.
EVOKER = {
    "evocation-savant": 3,
    "potent-cantrip": 3,
    "sculpt-spells": 6,
    "empowered-evocation": 10,
    "overchannel": 14,
}


def test_evoker_features_land_on_their_levels(loader):
    assert _levels(loader, "evoker") == EVOKER


@pytest.mark.parametrize("slug", ["berserker", "hand", "hunter", "thief", "land", "lore"])
def test_every_subclass_starts_its_features_at_level_three(loader, slug):
    """SRD 5.2: подкласс берётся на 3 уровне класса; раньше способностей нет."""
    levels = _levels(loader, slug)
    assert levels, slug
    assert min(levels.values()) >= 3, slug


def test_a_domain_subclass_grants_its_domain_spells(loader):
    """Life Domain выдаёт доменные заклинания на тех же уровнях, что и способности."""
    levels = _levels(loader, "life")
    for spell in ("bless", "cure-wounds", "aid", "revivify"):
        assert spell in levels, spell


def test_the_corpus_ships_one_subclass_per_class(loader):
    """НАЗВАННОЕ ОГРАНИЧЕНИЕ: 12 подклассов вместо ~48 в полной книге."""
    assert len(loader.list_slugs("subclasses")) == 12


def test_no_subclass_grants_a_feature_below_level_three(loader):
    for slug in loader.list_slugs("subclasses"):
        for ref in loader.get_subclass(slug).granted_features:
            assert ref.level >= 3, f"{slug}/{ref.slug} на уровне {ref.level}"

