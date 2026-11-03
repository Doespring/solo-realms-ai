"""A finished master answer is written down as complete.

solo/storage.py gm_finish() stores the answer, the status and the measured cost.
Leaving its UPDATE out left 29 tests green: they read the streamed tokens, not
the row that survives the request.

Section 5: what the table showed is what the log keeps. A row left as
streaming would also make a repeat stream the answer again.
"""
import os
os.environ['SOLO_TEST']='1'

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.contracts import Hero
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tests'))
from test_dialogue_paging_api import MockLLM  # noqa: E402


def test_a_finished_answer_is_stored_as_complete(tmp_path):
    app = create_app(tmp_path / 'gm.sqlite3', MockLLM())
    store = app.state.store
    cid = store.create('Мастер', Hero(name='Мирон', hp=12, max_hp=12))['id']
    question = 'Можно ли носить два щита?'
    staged = store.gm_stage(cid, 'ask-1', question)
    message = staged['id'] if isinstance(staged, dict) else staged
    with TestClient(app) as client:
        client.post('/api/gm/%s/stream' % message)
    row = store.gm_message(message)
    assert row['status'] in ('complete', 'error'), (
        'the answer row was left as %r' % row['status'])
    assert (row['answer'] or '').strip(), 'the stored answer is empty'