"""§3/§11: релиз движка собирается и его диджест воспроизводим."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope='module')
def release_module():
    os.environ.setdefault('SOLO_TEST', '1')
    import solo.nat20_content as nc

    return nc


def test_the_release_builds_from_the_pinned_corpus(release_module) -> None:
    """Путь через `solo/` реально собирается — это и есть поставка §14.10."""
    loader, digest, extra = release_module.release()
    assert loader is not None
    assert isinstance(digest, str) and len(digest) == 64
    assert extra


def test_the_release_digest_is_reproducible(release_module) -> None:
    """§14 п.5: два прогона дают один диджест, иначе версия не зафиксирована."""
    first = release_module.release()[1]
    second = release_module.release()[1]
    assert first == second


def test_a_changed_engine_file_breaks_the_release(release_module, tmp_path) -> None:
    """§13: порча запиненного файла обязана ловиться, а не проходить молча.

    `release()` проверяет установленное дерево (`dnd5e_engine.__file__`), а не
    git-копию в `vendor/`, поэтому портить надо именно установленный файл —
    правка vendor без синка для релиза невидима по делу.
    """
    from pathlib import Path

    import dnd5e_engine

    installed = Path(dnd5e_engine.__file__).parent
    target = next(path for path in sorted(installed.rglob('*.py')) if path.name != '__init__.py'
                  or path.parent != installed)
    original = target.read_bytes()
    try:
        target.write_bytes(original + b'\n# tampered by the acceptance test\n')
        with pytest.raises(Exception, match='Content differs from pinned SHA|Content changed after tree inventory'):
            release_module.release()
    finally:
        target.write_bytes(original)
    assert target.read_bytes() == original
    assert release_module.release()[1]

