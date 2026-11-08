"""Paging must not lose or repeat a turn while new turns arrive.

The cursor is (created, id). Reading walks strictly older, so a turn written
while the reader is on page two lands above the cursor and is simply not part
of this walk - it is not lost, it is newer than the walk began. What must not
happen is a turn appearing twice or a turn skipping out of the middle.
"""
import os
os.environ['SOLO_TEST']='1'

import json
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.storage import Store

from test_dialogue_paging_api import MockLLM


def build(tmp_path, count=12):
    db = tmp_path/'pg.sqlite3'
    app = create_app(db, MockLLM())
    store = Store(db)
    with store.connection(True) as c:
        c.execute("INSERT INTO campaigns(id,name,state,master,version,created) VALUES(?,?,?,?,?,?)",
                  ('c1', 'Чтение', json.dumps({'scene': 'двор'}), '{}', 1, 1000.0))
        for i in range(count):
            c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                      ('r%02d' % i, 'c1', 'q%02d' % i, 'Ход %d' % i, 'Ответ %d' % i, 'turn', '{}', 1000 + i))
    return TestClient(app), store


def walk(api, limit=4, pages=10):
    seen, cursor = [], None
    for _ in range(pages):
        params = {'limit': limit}
        if cursor is not None:
            params['before'] = json.dumps(cursor)
        page = api.get('/api/campaigns/c1/conversation', params=params).json()
        rows = page['conversation']
        seen += [r['id'] for r in rows]
        if not page['conversation_more'] or not page['conversation_cursor'] or not rows:
            break
        cursor = page['conversation_cursor']
    return seen


def test_a_new_turn_during_reading_never_appears_twice(tmp_path):
    api, store = build(tmp_path)
    first = api.get('/api/campaigns/c1/conversation', params={'limit': 4}).json()
    cursor = first['conversation_cursor']
    # A new turn arrives while the reader is still on the first page.
    with store.connection(True) as c:
        c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                  ('r99', 'c1', 'q99', 'Новый', 'Ответ новый', 'turn', '{}', 2000.0))
    # The walk starts from the first page, as a reader does, and continues with
    # the cursor that page offered.
    seen = [r['id'] for r in first['conversation']]
    pages = 0
    while cursor and pages < 10:
        pages += 1
        page = api.get('/api/campaigns/c1/conversation', params={'limit': 4, 'before': json.dumps(cursor)}).json()
        rows = page['conversation']
        seen += [r['id'] for r in rows]
        if not page['conversation_more'] or not rows:
            break
        cursor = page['conversation_cursor']
    assert len(seen) == len(set(seen)), 'a turn appeared twice'
    assert sorted(int(k[1:]) for k in seen) == list(range(12)), f'walk read {sorted(int(k[1:]) for k in seen)}'
    assert 'r99' not in seen, 'a turn newer than the walk began is not part of it'


def test_turns_sharing_a_timestamp_are_all_delivered_once(tmp_path):
    db = tmp_path/'same.sqlite3'
    app = create_app(db, MockLLM())
    store = Store(db)
    with store.connection(True) as c:
        c.execute("INSERT INTO campaigns(id,name,state,master,version,created) VALUES(?,?,?,?,?,?)",
                  ('c1', 'Чтение', json.dumps({'scene': 'двор'}), '{}', 1, 1000.0))
        # Ten turns, all at the same instant: only the id can order them.
        for i in range(10):
            c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                      ('s%02d' % i, 'c1', 'q%02d' % i, 'Ход %d' % i, 'Ответ %d' % i, 'turn', '{}', 5000.0))
    api = TestClient(app)
    seen = walk(api, limit=3)
    assert len(seen) == 10, f'read {len(seen)} of 10 turns that share a timestamp'
    assert len(set(seen)) == 10, 'a turn with a shared timestamp came back twice'
