"""A master message cut off by a restart must stop claiming to stream.

solo/storage.py:106 marks every gm_message still streaming when the store opens
as error. Leaving that line out left 3 tests green: they never carry a message
across a reopen, so the repair never fires.

The row and the event row are two halves of one lock: while either says
"streaming", the turn looks alive and the player is offered a stream that will
never continue. Section 9.2.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.storage import Store


def test_a_reopened_store_closes_a_streaming_master_message(tmp_path):
    path = tmp_path / 'gm-restart.sqlite3'
    store = Store(path)
    cid = store.create('Перезапуск', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    staged = store.gm_stage(cid, 'ask-1', 'Что видно во дворе?')['id']
    with store.connection(True) as db:
        db.execute("UPDATE gm_messages SET status='streaming' WHERE id=?", (staged,))
    assert store.gm_message(staged)['status'] == 'streaming'

    reopened = Store(path)
    row = reopened.gm_message(staged)
    assert row['status'] == 'error', (
        'the cut answer stayed %r after the restart' % row['status'])