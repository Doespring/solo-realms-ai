"""A generation cut off by a restart must not look unfinished.

solo/storage.py:105 marks every row still streaming when the store opens as
error. Leaving that UPDATE out left 3 tests green: none of them leaves a row
streaming across a reopen, so the repair never fires.

Section 9.2: after a restart the table must not offer to continue work whose
answer will never arrive.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.storage import Store


def _streaming_turn(store, cid):
    store.record_scenario(cid, 'walk-1', 'Иду во двор',
                          lambda state: [dict(type='scripted', text='Двор пуст.')])
    event = store.history(cid)[-1]['id']
    store.narrative(event, '', 'streaming', '')
    assert store.event(event)['narrative_status'] == 'streaming'
    return event


def test_a_reopened_store_marks_the_cut_generation_as_an_error(tmp_path):
    path = tmp_path / 'restart.sqlite3'
    store = Store(path)
    cid = store.create('Перезапуск', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    event = _streaming_turn(store, cid)

    reopened = Store(path)
    row = reopened.event(event)
    assert row['narrative_status'] == 'error', (
        'the cut generation stayed %r after the restart' % row['narrative_status'])
    assert row.get('error'), 'no reason was written for the player'