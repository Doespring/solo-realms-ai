"""A turn that waited for the model must notice a campaign that moved on.

state and the version are read before the model call (solo/game.py:729) and the
commit happens after it (solo/game.py:928). While the model thinks, the lock is
released on purpose, so another turn may commit in between. storage.stage()
catches the difference and refuses, which is why no update is lost - but the
player pays for the whole model wait before being told.

These tests pin both halves: the refusal exists, and it names the reason in
words instead of returning a bare exception.
"""
import os
os.environ['SOLO_TEST']='1'

import json
import tempfile
from pathlib import Path

import pytest

from solo.storage import Store
from solo.contracts import Hero


def store_with_campaign():
    store = Store(Path(tempfile.mkdtemp())/'v.sqlite3')
    cid = store.create('Проверка', Hero(name='Мирон', hp=12, max_hp=12))['id']
    return store, cid


def test_a_stale_version_is_refused_rather_than_written():
    store, cid = store_with_campaign()
    campaign = store.campaign(cid)
    stale = campaign['version']
    # Another turn commits while the first one is waiting for the model: the
    # campaign version moves on exactly as a real commit would move it.
    with store.connection(True) as c:
        c.execute('UPDATE campaigns SET state=?,version=version+1 WHERE id=?', (json.dumps(campaign['state']), cid))
    with pytest.raises(ValueError) as error:
        store.stage(cid, 'req-stale', 'осматриваюсь', type('P', (), {'model_dump_json': lambda self: '{}'})(), stale, '{}')
    assert 'Сцена изменилась' in str(error.value), 'the refusal must say what happened'


def test_the_same_version_is_accepted():
    store, cid = store_with_campaign()
    campaign = store.campaign(cid)
    action = store.stage(cid, 'req-ok', 'осматриваюсь', type('P', (), {'model_dump_json': lambda self: '{}'})(),
                         campaign['version'], '{}')
    assert action['request_id'] == 'req-ok'


def test_a_repeated_request_is_not_executed_twice():
    store, cid = store_with_campaign()
    campaign = store.campaign(cid)
    plan = type('P', (), {'model_dump_json': lambda self: '{}'})()
    first = store.stage(cid, 'req-same', 'осматриваюсь', plan, campaign['version'], '{}')
    second = store.stage(cid, 'req-same', 'осматриваюсь', plan, campaign['version'], '{}')
    assert first['id'] == second['id'], 'a repeated request must reuse its action'
