"""An older page of history must not draw a turn that is already in the journal.

solo/app.py:302 marks a conversation row with journal_event in the campaign
response; :352 does the same for GET /campaigns/{id}/conversation, which is the
route web/play.js:90 actually calls when the player asks for older dialogue.
Removing only the second marking left 14 tests green: they read the campaign
response, never the older page. Section 5: one line of the player is one public
turn, on every page.
"""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import MockLLM
from scripts.command_smoke import setup


def _campaign(tmp_path):
    store, campaign, game = setup(tmp_path / 'db', llm=MockLLM())
    return create_app(store.path, MockLLM()), store, campaign


def test_an_older_page_links_the_row_to_its_journal(tmp_path):
    app, store, campaign = _campaign(tmp_path)
    journal = store.record_scenario(campaign, 'probe-1', 'Спрашиваю про мост',
                                    lambda state: [dict(type='scripted', kind='conversation',
                                                        text='Дорн отвечает.')])
    store.converse(campaign, 'probe-1', 'Спрашиваю про мост', 'Дорн отвечает.',
                   'notice', dict(journal_id=journal['id']))
    with TestClient(app) as client:
        page = client.get(f'/api/campaigns/{campaign}/conversation?limit=5').json()
    rows = [r for r in page['conversation'] if r['request_id'] == 'probe-1']
    assert rows, 'the turn is not on the older page at all'
    assert rows[0].get('journal_event'), (
        'the older page lost the link, so the turn is drawn twice: '
        + str(rows[0])[:160])
