"""An undone turn must not accept a new narrative it no longer owns.

solo/storage.py:299 reads the event with 'AND undone=0' and refuses to write
into it; dropping that filter left every undo test green. Section 11: an undone
turn is gone, and a late writer must not quietly finish it.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.storage import Store
from solo.contracts import Hero
from solo.rulesets import BUILTINS


def _store(tmp_path):
    store = Store(tmp_path / 'undo.sqlite3')
    cid = store.create('Отмена', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    event = store.record_scenario(cid, 'probe-1', 'Осматриваю зал',
                                  lambda state: [dict(type='scripted', text='Зал пуст.')])
    store.undo(cid)
    return store, cid, event


def test_a_late_narrative_does_not_finish_an_undone_turn(tmp_path):
    store, cid, event = _store(tmp_path)
    with pytest.raises(ValueError) as refused:
        store.narrative(event['id'], 'Поздний текст.', 'complete')
    assert 'откачено' in str(refused.value), str(refused.value)
