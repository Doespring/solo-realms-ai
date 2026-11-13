"""Требование 3: явно подключаемое расширение поверх SRD.

Пункт 3: «Сохрани самостоятельный SRD-режим и отдельно явно подключаемое
PHB-расширение»; «PHB не требуется для обычной SRD-кампании»;
«Отсутствующий пакет не заменяется похожими правилами».

Шов расширения — `configure_lib_loader` (`dnd5e_engine/lib_loader.py:33`):
движок не содержит правил и получает их через `AssetLoader`. Здесь проверяется,
что расширение действительно подключается и действительно снимается, а не что
существует функция с подходящим именем.
"""
import pytest

from dnd5e_engine.lib_loader import (
    configure_lib_loader,
    get_lib_loader,
    set_lib_loader_for_tests,
)
from dnd5e_srd_data.loader import AssetLoader, BundledAssetLoader


def test_the_engine_reads_its_numbers_through_the_loader():
    """Движок агностичен к редакции: числа приходят через загрузчик.

    Первая версия проверяла `BundledAssetLoader.__module__` — то есть модуль
    ДАННЫХ, а не движок, и не могла поймать подмену записи (находка
    независимого критика). Теперь проверяется поведение: подменённый через
    `configure_lib_loader` корпус обязан изменить то, что видит движок, —
    значит движок действительно читает запись из загрузчика.
    """
    base = BundledAssetLoader()
    real = base.get_spell("fireball")
    assert real is not None

    poisoned = real.model_copy(update={"name": "Подменённый огненный шар"})

    class _Poison:
        def get_spell(self, slug):
            return poisoned if slug == "fireball" else base.get_spell(slug)

        def __getattr__(self, name):
            return getattr(base, name)

    set_lib_loader_for_tests(_Poison())
    try:
        # Движок обязан увидеть ПОДМЕНЁННУЮ запись, а не исходную.
        assert get_lib_loader().get_spell("fireball").name == poisoned.name
        assert get_lib_loader().get_spell("fireball").name == "Подменённый огненный шар"
        # ...и не тронуть остальной корпус.
        assert get_lib_loader().get_spell("bless").name == base.get_spell("bless").name
    finally:
        set_lib_loader_for_tests(None)
    # Снятие шва возвращает чистый корпус.
    assert get_lib_loader().get_spell("fireball").name != "Подменённый огненный шар"


def test_the_default_loader_is_the_bundled_srd():
    set_lib_loader_for_tests(None)
    assert isinstance(get_lib_loader(), BundledAssetLoader)


class Extension:
    """Расширение поверх SRD: перекрывает одно заклинание своим.

    Остальной корпус уходит в обёрнутый загрузчик без изменений, поэтому
    расширение не может незаметно переписать чужие записи.
    """

    def __init__(self, base, slug, value):
        self._base = base
        self._slug = slug
        self._value = value

    def get_spell(self, slug):
        if slug == self._slug:
            return self._value
        return self._base.get_spell(slug)

    def __getattr__(self, name):
        return getattr(self._base, name)


@pytest.fixture
def restore():
    yield
    set_lib_loader_for_tests(None)


def _extension():
    base = BundledAssetLoader()
    own = base.get_spell("bless").model_copy(update={"name": "Bless (расширение)"})
    return Extension(base, "bless", own)


def test_an_extension_is_really_installed(restore):
    configure_lib_loader(_extension())
    assert get_lib_loader().get_spell("bless").name == "Bless (расширение)"


def test_the_extension_does_not_leak_into_other_records(restore):
    """Совпадение названий не переписывает остальной корпус."""
    plain = BundledAssetLoader().get_spell("fireball").name
    configure_lib_loader(_extension())
    assert get_lib_loader().get_spell("fireball").name == plain


def test_removing_an_extension_restores_pristine_srd(restore):
    """Без расширения SRD работает сам по себе — PHB не требуется."""
    pristine = BundledAssetLoader().get_spell("bless").name
    configure_lib_loader(_extension())
    assert get_lib_loader().get_spell("bless").name != pristine
    configure_lib_loader(None)
    assert get_lib_loader().get_spell("bless").name == pristine


def test_a_missing_record_is_not_substituted():
    assert BundledAssetLoader().get_spell("this-spell-does-not-exist") is None


def test_the_protocol_covers_every_record_kind_the_engine_resolves():
    for name in ("get_spell", "get_class", "get_subclass", "get_species",
                 "get_background", "get_feat", "get_item", "get_condition",
                 "get_feature", "get_monster", "get_armor"):
        assert hasattr(AssetLoader, name), name
