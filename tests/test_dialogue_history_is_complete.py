"""HTTP history paging survives a new turn while the player is reading back.

The screen-side merge (web/play.js) is covered separately by
 tests/test_dialogue_paging_merge.py; this file covers the server contract.
"""
import os
os.environ['SOLO_TEST']='1'

import json
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Hero
from solo.llm import MockLLM


def _client(tmp_path, turns=120):
    app = create_app(tmp_path / 'solo.sqlite3', MockLLM())
    store = app.state.store
    cid = store.create('Perepiska', Hero(name='Miron', hp=12, max_hp=12))['id']
    with store.connection() as c:
        for i in range(turns):
            c.execute('INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) '
                      'VALUES (?,?,?,?,?,?,?,?)',
                      ('c%03d' % i, cid, 'r%d' % i, 'turn %d' % i, 'reply %d' % i, 'notice', '{}', 1000.0 + (i // 3)))
    return TestClient(app), cid, store


def _all_pages(client, cid):
    body = client.get('/api/campaigns/' + cid).json()
    seen = [r['id'] for r in body['conversation']]
    cursor = body['conversation_cursor']
    while cursor:
        page = client.get('/api/campaigns/%s/conversation' % cid,
                          params=dict(before=json.dumps(cursor), limit=9)).json()
        if not page['conversation']:
            break
        seen = [r['id'] for r in page['conversation']] + seen
        cursor = page['conversation_cursor']
    return seen


def test_every_stored_turn_is_reachable(tmp_path):
    client, cid, _ = _client(tmp_path)
    seen = _all_pages(client, cid)
    assert seen == ['c%03d' % i for i in range(120)]


def test_a_new_turn_during_reading_does_not_shift_the_pages(tmp_path):
    client, cid, store = _client(tmp_path, turns=120)
    newest = client.get('/api/campaigns/' + cid).json()
    older = client.get('/api/campaigns/%s/conversation' % cid,
                       params=dict(before=json.dumps(newest['conversation_cursor']), limit=40)).json()
    # The new turn lands inside the already-fetched range: a page that ignores
    # its cursor would shift here, while one addressed by (created, id) cannot.
    with store.connection() as c:
        c.execute('INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) '
                  'VALUES (?,?,?,?,?,?,?,?)',
                  ('c1000', cid, 'r1000', 'inserted', 'inserted reply', 'notice', '{}', 1003.5))
    # The page already fetched still names the same rows and the same cursor.
    again = client.get('/api/campaigns/%s/conversation' % cid,
                       params=dict(before=json.dumps(newest['conversation_cursor']), limit=40)).json()
    assert [r['id'] for r in again['conversation']] == [r['id'] for r in older['conversation']]
    assert again['conversation_cursor'] == older['conversation_cursor']


def test_model_context_limit_does_not_shorten_the_readable_dialogue(tmp_path):
    client, cid, store = _client(tmp_path, turns=300)
    body = client.get('/api/campaigns/' + cid).json()
    assert body['conversation_total'] == 300
    assert len(_all_pages(client, cid)) == 300