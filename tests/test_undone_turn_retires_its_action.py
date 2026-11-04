"""An undone turn must also retire the action that produced it.

solo/storage.py:553 marks the action status='undone'. Removing that line left 17
tests green: none of them looked at the action after undo. Section 11: the
bookkeeping must not claim a turn is still applied when it was taken back.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.storage import Store


def test_the_action_of_an_undone_turn_is_retired(tmp_path):
    store = Store(tmp_path / 'a.sqlite3')
    cid = store.create('Откат', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    event = store.record_scenario(cid, 'r1', 'Осматриваюсь',
                                  lambda state: [dict(type='scripted', text='Тихо.')])
    action = event['action']
    assert store.action(action)['status'] == 'applied', 'the turn did not apply'

    store.undo(cid)
    assert store.action(action)['status'] == 'undone', (
        'the action still claims to be applied after the undo')
