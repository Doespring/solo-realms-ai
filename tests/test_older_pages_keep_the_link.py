"""An older page must still say which journal event each turn belongs to.

Section 5 requires historical double records to be handled without deleting the
canonical event or the memory. The server keeps both and marks the link, and the
marker is 'journal_event'. The earlier fix for §5 stopped shipping the internal
'context' column; the public link had to survive that change, or every older page
would draw a turn a second time beside the event that already shows it.
"""
import os
os.environ['SOLO_TEST']='1'

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.storage import Store
from test_dialogue_paging_api import MockLLM

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_campaign  # noqa: E402


def setup(tmp_path):
    store, campaign, _ = make_campaign(tmp_path)
    return TestClient(create_app(store.path, MockLLM())), store, campaign


def test_the_link_is_public_and_the_bookkeeping_is_not(tmp_path):
    api, store, campaign = setup(tmp_path)
    journal = store.record_scenario(campaign, 'req-1', 'Смотрю на дверь',
                                    lambda s: [dict(type='scripted', kind='notice', text='Дверь открыта')])
    with store.connection(True) as c:
        store.converse(campaign, 'req-1', 'Смотрю на дверь', 'Дверь открыта.', 'notice',
                       dict(journal_id=journal['id']), db=c)
    rows = api.get('/api/campaigns/%s/conversation?limit=10' % campaign).json()['conversation']
    assert len(rows) == 1
    row = rows[0]
    assert row.get('journal_event') == str(journal['id']), 'the page cannot merge the pair without the link'
    assert 'context' not in row, 'the internal record must not travel'


def test_nothing_is_deleted_when_a_pair_exists(tmp_path):
    api, store, campaign = setup(tmp_path)
    journal = store.record_scenario(campaign, 'req-2', 'Жду',
                                    lambda s: [dict(type='scripted', kind='notice', text='Тихо')])
    with store.connection(True) as c:
        store.converse(campaign, 'req-2', 'Жду', 'Тихо.', 'notice', dict(journal_id=journal['id']), db=c)
        events_before = c.execute("SELECT COUNT(*) FROM events WHERE campaign=?", (campaign,)).fetchone()[0]
        talk_before = c.execute("SELECT COUNT(*) FROM conversations WHERE campaign=?", (campaign,)).fetchone()[0]
    api.get('/api/campaigns/%s/conversation?limit=10' % campaign)
    api.get('/api/campaigns/%s' % campaign)
    with store.connection(True) as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE campaign=?", (campaign,)).fetchone()[0] == events_before
        assert c.execute("SELECT COUNT(*) FROM conversations WHERE campaign=?", (campaign,)).fetchone()[0] == talk_before
