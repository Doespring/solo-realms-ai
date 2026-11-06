"""The campaign response must say how to read older dialogue.

Section 10 of the audit found 56 stored rows and 50 served, with no way to ask
for the rest. The storage cursor is covered by
tests/test_full_dialogue_is_readable.py; this checks the HTTP contract the
browser actually uses: a cursor, a total, and a page endpoint that meets the
newest page without gaps or repeats.
"""
import json
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Hero
from solo.llm import MockLLM


def _client(tmp_path, turns=56):
    app = create_app(tmp_path / 'solo.sqlite3', MockLLM())
    store = app.state.store
    cid = store.create('Переписка', Hero(name='Мирон', hp=12, max_hp=12))['id']
    with store.connection() as c:
        for i in range(turns):
            c.execute('INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) '
                      'VALUES (?,?,?,?,?,?,?,?)',
                      ('c%03d' % i, cid, 'r%d' % i, 'ход %d' % i, 'ответ %d' % i, 'notice', '{}', 1000.0 + (i // 4)))
    return TestClient(app), cid


def test_campaign_names_the_cursor_and_the_total(tmp_path):
    client, cid = _client(tmp_path)
    body = client.get('/api/campaigns/' + cid).json()
    assert body['conversation_total'] == 56
    assert body['conversation_more'] is True
    assert len(body['conversation']) == 50
    assert body['conversation_cursor'] == [body['conversation'][0]['created'], body['conversation'][0]['id']]


def test_older_page_meets_the_newest_one_exactly(tmp_path):
    client, cid = _client(tmp_path)
    newest = client.get('/api/campaigns/' + cid).json()
    seen = [r['id'] for r in newest['conversation']]
    cursor = newest['conversation_cursor']
    while cursor:
        page = client.get('/api/campaigns/%s/conversation' % cid,
                          params=dict(before=json.dumps(cursor), limit=7)).json()
        if not page['conversation']:
            break
        seen = [r['id'] for r in page['conversation']] + seen
        cursor = page['conversation_cursor']
    assert len(seen) == len(set(seen)), 'a row appeared twice'
    assert seen == ['c%03d' % i for i in range(56)], 'a row was skipped or misplaced'


def test_a_broken_cursor_is_refused_not_ignored(tmp_path):
    client, cid = _client(tmp_path)
    assert client.get('/api/campaigns/%s/conversation' % cid, params=dict(before='не курсор')).status_code == 400


def test_short_history_reports_nothing_more(tmp_path):
    client, cid = _client(tmp_path, turns=3)
    body = client.get('/api/campaigns/' + cid).json()
    assert body['conversation_total'] == 3 and body['conversation_more'] is False
    assert len(body['conversation']) == 3
