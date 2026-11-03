"""The history cursor the server hands out must be one it accepts back.

An earlier version read the cursor with 'created,ident=json.loads(before)', but
the cursor goes out as a JSON pair ([created, id]), so unpacking a list into two
names always failed and every second page answered 400. The first page worked,
which is exactly why the defect could look like a passing feature.

These tests send the cursor exactly as the response gives it.
"""
import os
os.environ['SOLO_TEST']='1'

import json
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.storage import Store

from test_dialogue_paging_api import MockLLM


def client(tmp_path):
    import time
    db = tmp_path/'paging.sqlite3'
    app = create_app(db, MockLLM())
    store = Store(db)
    with store.connection(True) as c:
        c.execute("INSERT INTO campaigns(id,name,state,master,version,created) VALUES(?,?,?,?,?,?)",
                  ('c1', 'Чтение', json.dumps({'scene': 'двор'}), '[]', 1, time.time()))
        for i in range(12):
            c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                      ('r%02d' % i, 'c1', 'req-%02d' % i, 'Ход %d' % i, 'Ответ %d' % i, 'turn', '{}', 1000 + i))
    return TestClient(app), store


def test_the_returned_cursor_is_accepted_back(tmp_path):
    api, _ = client(tmp_path)
    first = api.get('/api/campaigns/c1/conversation?limit=5').json()
    cursor = first['conversation_cursor']
    assert cursor, 'a page that is not the last must offer a way back'
    again = api.get('/api/campaigns/c1/conversation', params={'limit': 5, 'before': json.dumps(cursor)})
    assert again.status_code == 200, again.text


def test_pages_cover_every_turn_once(tmp_path):
    api, _ = client(tmp_path)
    seen, cursor = [], None
    for _ in range(20):
        params = {'limit': 5}
        if cursor is not None:
            params['before'] = json.dumps(cursor)
        page = api.get('/api/campaigns/c1/conversation', params=params).json()
        rows = page['conversation']
        seen += [row['id'] for row in rows]
        if not page['conversation_more'] or not page['conversation_cursor'] or not rows:
            break
        cursor = page['conversation_cursor']
    assert len(seen) == 12, f'read {len(seen)} of 12 turns'
    assert len(set(seen)) == 12, 'a turn came back twice'


def test_a_nonsense_cursor_is_refused_clearly(tmp_path):
    api, _ = client(tmp_path)
    bad = api.get('/api/campaigns/c1/conversation', params={'before': 'не-курсор'})
    assert bad.status_code == 400
    assert bad.json()['detail']




def big_client(tmp_path):
    import time
    db = tmp_path/'big.sqlite3'
    app = create_app(db, MockLLM())
    store = Store(db)
    with store.connection(True) as c:
        c.execute("INSERT INTO campaigns(id,name,state,master,version,created) VALUES(?,?,?,?,?,?)",
                  ('big', 'Долгая', json.dumps({'scene': 'двор'}), '{}', 1, time.time()))
        for i in range(250):
            c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                      ('r%03d' % i, 'big', 'q%03d' % i, 'ход', 'ответ', 'turn', '{}', 1000 + i))
    return TestClient(app), store

def test_one_page_never_returns_the_whole_history(tmp_path):
    """A reader asks for 5000; the server still sends a readable page.

    Without the upper bound a single request would hand back every turn of a
    long campaign - the history would stop being paged at all, which is what
    section 9.3 exists to prevent.
    """
    api, _ = client(tmp_path)
    page = api.get('/api/campaigns/c1/conversation', params={'limit': 5000}).json()
    assert len(page['conversation']) <= 200, 'the page grew past its bound'
    # With all twelve turns fitting, there is nothing older to offer.
    assert len(page['conversation']) == 12
    assert page['conversation_more'] is False


def test_a_nonsense_limit_still_returns_something(tmp_path):
    """Zero or negative limits must not produce an empty, unusable answer."""
    api, _ = client(tmp_path)
    for bad in (0, -5):
        page = api.get('/api/campaigns/c1/conversation', params={'limit': bad})
        assert page.status_code == 200, page.text
        assert page.json()['conversation'], 'a page must carry at least one turn'


def test_a_huge_limit_is_cut_at_the_bound_and_offers_more(tmp_path):
    """250 turns, a request for 5000: the page stops at the bound and says more exist.

    This is the case where the bound actually decides the answer, so lowering it
    or removing it changes what the reader gets.
    """
    api, store = big_client(tmp_path)
    page = api.get('/api/campaigns/big/conversation', params={'limit': 5000}).json()
    assert len(page['conversation']) == 200, 'the page must stop at its bound'
    assert page['conversation_more'] is True, '250 turns cannot fit in one page'
    older = api.get('/api/campaigns/big/conversation',
                    params={'limit': 5000, 'before': json.dumps(page['conversation_cursor'])}).json()
    assert len(older['conversation']) == 50, 'the rest is exactly what did not fit'
    assert older['conversation_more'] is False
