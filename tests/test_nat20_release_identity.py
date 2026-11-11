"""Регрессионная проверка принятого решения об идентичности релиза.

Расширение Bless привязано к родителю `374afd1a…`, которого нет ни в текущем рабочем
дереве, ни в истории (в коммите `9723a04`, где менялся манифест релиза, каталога
`vendor/nat20` не существует вовсе). Миграция не выполняется, потому что совместимость
старых кампаний на новых байтах не доказана. Ниже закреплено именно это: понятный отказ
и НЕИЗМЕННОСТЬ сохранения. См. docs/RELEASE_IDENTITY_DECISION.md.
"""
import pytest

from solo.nat20_api import Nat20GameEngine
from solo.nat20_bless import PROFILE
from solo.storage import Store

OLD = '374afd1a3898e089e023130cc342a0f3c6fe88a75bf4f8ee7e3bf865852abf75'


def test_bless_release_is_refused_without_touching_storage(tmp_path, monkeypatch):
    # The flag is set through monkeypatch, so it is undone for every later test.
    # Setting it in os.environ here would leak into the rest of the run and
    # silently add cast_spell/drop_concentration to the model's tool schemas.
    monkeypatch.setenv('SOLO_TEST', '1')
    monkeypatch.setenv('SOLO_NAT20_BLESS_STAND', '1')
    api = Nat20GameEngine(Store(tmp_path / 'identity.sqlite3'))
    before = api.store.list_campaigns()
    with pytest.raises(ValueError, match='parent/edition mismatch'):
        api.create_campaign('Pending caster', definition_id=PROFILE, seed=5)
    # The refusal is total: nothing was created and nothing was written.
    assert api.store.list_campaigns() == before == []


def test_the_old_digest_is_still_recorded_and_is_not_the_live_one():
    import json
    from pathlib import Path

    from solo.nat20_content import release

    fixture = json.loads((Path(__file__).parent / 'fixtures/nat20-preview7-fighter-combat.json')
                         .read_text(encoding='utf-8'))
    assert fixture['release_digest'] == OLD
    live = release()[1]
    assert live != OLD
    assert len(live) == 64
    # A digest is lowercase hex: parsing it proves the shape, not just the length.
    assert all(c in '0123456789abcdef' for c in live)

