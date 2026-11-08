"""Three identical player lines are three turns, not one repeated row.

Section 5 of the task says lines that read the same but are separate turns must
stay separate. A dedupe by text would swallow them; a dedupe by request id keeps
them. These tests take the side the player sees: the stored rows, the page the
server hands out, and how the screen keys them.
"""
import os
os.environ['SOLO_TEST']='1'

import re

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.storage import Store
from test_dialogue_paging_api import MockLLM

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def client(tmp_path):
    db = tmp_path/'repeat.sqlite3'
    app = create_app(db, MockLLM())
    store = Store(db)
    with store.connection(True) as c:
        c.execute("INSERT INTO campaigns(id,name,state,master,version,created) VALUES(?,?,?,?,?,?)",
                  ('c1', 'Повтор', '{}', '{}', 1, 1000.0))
    return TestClient(app), store


def record(store, request_id, text):
    with store.connection(True) as c:
        return store.converse('c1', request_id, text, 'Мастер отвечает.', 'notice', {}, db=c)


def test_three_identical_lines_are_three_rows(tmp_path):
    _, store = client(tmp_path)
    first = record(store, 'r1', 'Смотрю на дверь.')
    second = record(store, 'r2', 'Смотрю на дверь.')
    third = record(store, 'r3', 'Смотрю на дверь.')
    assert len({first['id'], second['id'], third['id']}) == 3, 'three turns must not collapse into one row'
    assert store.conversation_count('c1') == 3


def test_the_page_shows_all_three(tmp_path):
    api, store = client(tmp_path)
    for i in range(3):
        record(store, 'r%d' % i, 'Смотрю на дверь.')
    rows = api.get('/api/campaigns/c1/conversation?limit=10').json()['conversation']
    assert len(rows) == 3
    assert len({row['id'] for row in rows}) == 3
    assert all(row['original'] == 'Смотрю на дверь.' for row in rows)


def test_the_screen_keys_them_apart(tmp_path):
    """The screen must key by id, never by text, or it would draw one line."""
    api, store = client(tmp_path)
    for i in range(3):
        record(store, 'r%d' % i, 'Смотрю на дверь.')
    rows = api.get('/api/campaigns/c1/conversation?limit=10').json()['conversation']
    source = open(HERE + '/web/play.js', encoding='utf-8').read()
    assert 'request_id' in source or 'dataset.archiveKey' in source, 'the screen must key on an id'
    assert not re.search(r"new Set\([^)]*\.text", source), 'text must never be the dedupe key'
    assert len({row.get('request_id') or row['id'] for row in rows}) == 3

def test_the_same_second_does_not_collapse_the_order(tmp_path):
    """Three turns written in the same second still come back in one order.

    The rows above land on different timestamps, so ordering by time alone is
    enough for them. Here every row gets the same 'created', which is what
    happens to a fast player: only the id can decide, and it must be used on
    both the first page and the page before it.
    """
    import json
    api, store = client(tmp_path)
    with store.connection(True) as c:
        for i in range(3):
            c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      ('cmp%02d' % i, 'c1', 'q%d' % i, 'Смотрю на дверь.', 'Мастер отвечает.',
                       'turn', json.dumps({}), 2000.0))
    page = api.get('/api/campaigns/c1/conversation', params={'limit': 10}).json()
    order = [row['id'] for row in page['conversation']]
    assert order == ['cmp00', 'cmp01', 'cmp02'], order
    first = api.get('/api/campaigns/c1/conversation', params={'limit': 2}).json()
    cursor = first['conversation_cursor']
    rest = api.get('/api/campaigns/c1/conversation',
                   params={'limit': 2, 'before': json.dumps(cursor)}).json()
    seen = [row['id'] for row in first['conversation']] + [row['id'] for row in rest['conversation']]
    assert len(seen) == len(set(seen)) == 3, seen
