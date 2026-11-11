"""Restoring a checkpoint must not leave undone turns in the search index."""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.storage import Store


def _hits(store, cid, word):
    with store.connection() as db:
        return [r['event_id'] for r in db.execute(
            "SELECT event_id FROM journal_fts WHERE campaign=? AND journal_fts MATCH ?",
            (cid, word)).fetchall()]


def test_a_restore_drops_the_turns_it_rolled_back(tmp_path):
    store = Store(tmp_path / 'restore.sqlite3')
    cid = store.create('Откат', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    store.record_scenario(cid, 'a', 'Осмотреться',
                          lambda state: [dict(type='scripted', text='Двор пуст.')])
    first = store.history(cid)[-1]['id']
    store.narrative(first, 'Двор пуст.', 'complete')
    mark = store.save(cid, 'Until the courtyard')
    assert _hits(store, cid, 'Двор'), 'the index missed a complete turn'

    store.record_scenario(cid, 'b', 'Войти в башню',
                          lambda state: [dict(type='scripted', text='Башня темна.')])
    second = store.history(cid)[-1]['id']
    store.narrative(second, 'Башня темна.', 'complete')

    store.restore(cid, mark)

    assert second not in _hits(store, cid, 'Башня'), (
        'a rolled back turn is still searchable')
    assert first in _hits(store, cid, 'Двор'), 'the restore dropped a kept turn'