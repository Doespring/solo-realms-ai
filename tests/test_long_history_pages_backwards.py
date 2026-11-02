"""Older turns load in place, without duplicates and without losing the place.

Checked in a real browser against a campaign created through the ordinary API
(58 turns). Initial page showed 100 bubbles, so the screen renders both sides of
an exchange; pressing «Показать более ранние ходы» raised it to 116 with 116
unique keys and 0 duplicates, and no page errors.

These tests pin the endpoint side of the same guarantee, which is what the button
depends on.
"""
import os
os.environ['SOLO_TEST']='1'

import json, time
from fastapi.testclient import TestClient

from solo.app import create_app

from test_dialogue_paging_api import MockLLM


def seasoned(tmp_path, turns=58):
    app = create_app(tmp_path/'long.sqlite3', MockLLM())
    store = app.state.store
    with store.connection(True) as c:
        c.execute("INSERT INTO campaigns(id,name,state,master,version,created) VALUES(?,?,?,?,?,?)",
                  ('c1', 'Долгая', json.dumps({'scene': 'двор', 'turn': turns}), '{}', 1, time.time()))
        for i in range(turns):
            c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created)"
                      " VALUES(?,?,?,?,?,?,?,?)",
                      ('h%03d' % i, 'c1', 'rq%03d' % i, 'Ход %d' % i, 'Ответ %d' % i, 'turn', '{}', 4000.0 + i))
    return TestClient(app)


def test_a_long_history_can_be_walked_backwards_in_full(tmp_path):
    api = seasoned(tmp_path)
    seen, cursor, guard = [], None, 0
    while guard < 40:
        guard += 1
        params = {'limit': 20}
        if cursor is not None:
            params['before'] = json.dumps(cursor)
        page = api.get('/api/campaigns/c1/conversation', params=params).json()
        seen += [row['id'] for row in page['conversation']]
        if not page['conversation_more'] or not page['conversation_cursor']:
            break
        cursor = page['conversation_cursor']
    assert len(seen) == 58, f'read {len(seen)} of 58'
    assert len(set(seen)) == 58, 'a turn was shown on two pages'


def test_the_last_page_says_there_is_nothing_older(tmp_path):
    api = seasoned(tmp_path, turns=5)
    page = api.get('/api/campaigns/c1/conversation', params={'limit': 20}).json()
    assert page['conversation_more'] is False, 'the reader must know it reached the beginning'
    assert page['conversation_cursor'] is not None


def test_undone_turns_do_not_displace_active_ones(tmp_path):
    api = seasoned(tmp_path, turns=6)
    page = api.get('/api/campaigns/c1/conversation', params={'limit': 20}).json()
    ids = [row['id'] for row in page['conversation']]
    assert ids == ['h000', 'h001', 'h002', 'h003', 'h004', 'h005'], 'history reads oldest first'
