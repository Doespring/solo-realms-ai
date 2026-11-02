"""Older pages must carry the same link the first page uses.

c2c6598 removed 'context' from the paged answer, which was right, but it also
removed 'journal_event' - and web/play.js:61 decides whether a turn was already
shown by the journal through exactly that field. Without it an older page draws
the turn a second time beside the event that already shows it. The verifier
caught this; the test below is what keeps it caught.
"""
import os
os.environ['SOLO_TEST']='1'

import json
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.contracts import Hero


def client(tmp_path):
    app = create_app(tmp_path/'l.sqlite3', __import__('solo.llm', fromlist=['MockLLM']).MockLLM())
    store = app.state.store
    cid = store.create('Переписка', Hero(name='Мирон', hp=12, max_hp=12))['id']
    return TestClient(app), store, cid


def test_the_paged_answer_still_links_a_turn_to_its_journal_event(tmp_path):
    api, store, cid = client(tmp_path)
    with store.connection(True) as c:
        c.execute("INSERT INTO actions(id,campaign,request_id,original,plan,status,version,created) VALUES(?,?,?,?,?,?,?,?)",
                  ('a1', cid, 'q1', 'Ход', '{}', 'resolved', 1, 1000.0))
        c.execute("INSERT INTO events(id,seq,action,campaign,before_state,after_state,results,narrative,narrative_status,error,undone,created,narrative_data) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ('ev001', 1, 'a1', cid, '{}', '{}', '[]', 'Ход', 'complete', '', 0, 1000.0, '{}'))
        c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                  ('c1', cid, 'q1', 'Ход', 'Ответ', 'turn', json.dumps({'journal_id': 'ev001'}), 1000.0))
    page = api.get('/api/campaigns/%s/conversation' % cid, params={'limit': 10}).json()
    row = page['conversation'][0]
    assert row.get('journal_event') == 'ev001', 'without the link the screen draws the turn twice'


def test_the_link_is_not_invented_when_the_event_is_absent(tmp_path):
    api, store, cid = client(tmp_path)
    with store.connection(True) as c:
        c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                  ('c9', cid, 'q9', 'Ход', 'Ответ', 'turn', json.dumps({'journal_id': 'missing'}), 1000.0))
    page = api.get('/api/campaigns/%s/conversation' % cid).json()
    assert 'journal_event' not in page['conversation'][0]


def test_bookkeeping_still_stays_on_the_server(tmp_path):
    api, store, cid = client(tmp_path)
    with store.connection(True) as c:
        c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                  ('c1', cid, 'q1', 'Ход', 'Ответ', 'turn', '{}', 1000.0))
    page = api.get('/api/campaigns/%s/conversation' % cid).json()
    assert 'context' not in page['conversation'][0], 'the internal link stays here'
