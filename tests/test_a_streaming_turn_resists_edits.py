"""A half-streamed narrative cannot be corrected yet.

solo/storage.py edit_narrative() refuses when the event is still streaming:
the stream is going to write its own text, so a correction inserted now would
be overwritten or split. Replacing that guard with pass left nine tests green.

Section 9.2: long operations must not race with the player's edits.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.storage import Store


def _event(store, cid):
    store.record_scenario(cid, 'turn-1', 'Осматриваюсь',
                          lambda state: [dict(type='scripted', text='Тихо.')])
    return store.history(cid)[-1]['id']


def _set_status(store, event, status):
    with store.connection(True) as c:
        c.execute('UPDATE events SET narrative_status=? WHERE id=?', (status, event))


def test_a_finished_narration_can_be_corrected(tmp_path):
    store = Store(tmp_path / 'edit.sqlite3')
    cid = store.create('Правка', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    event = _event(store, cid)
    _set_status(store, event, 'complete')
    store.edit_narrative(event, 'Исправленный текст.')
    assert store.event(event)['narrative'] == 'Исправленный текст.'


def test_a_streaming_narration_cannot_be_corrected(tmp_path):
    store = Store(tmp_path / 'edit2.sqlite3')
    cid = store.create('Правка', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    event = _event(store, cid)
    _set_status(store, event, 'streaming')
    with pytest.raises(ValueError, match='остановите генерацию'):
        store.edit_narrative(event, 'Влез посреди потока.')
