"""An engine without a catalog search must refuse clearly, not crash."""
import os
os.environ['SOLO_TEST']='1'

import pytest

import solo.game as game_module
from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.storage import Store


def test_a_missing_catalog_search_is_refused(tmp_path, monkeypatch):
    store = Store(tmp_path / 'catalog.sqlite3')
    cid = store.create('Каталог', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    engine = game_module.Game(store, None)
    lookup = getattr(engine, 'command_lookup', None) or engine.compiler.command_lookup

    class Bare:
        """A backend that offers no search at all."""

    monkeypatch.setattr('solo.command_backend.command_backend',
                        lambda store, current: Bare())

    current = store.campaign(cid)
    with pytest.raises(ValueError) as caught:
        lookup(current, ['longsword'])
    assert 'поиска по каталогу' in str(caught.value), (
        'refused for another reason: %s' % caught.value)