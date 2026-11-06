"""The search index must never answer with text the player can no longer read.

solo/storage.py keeps journal_fts in step with events: it clears the row before
rewriting narrative text (:313, :329) and on undo (:555). No MATCH query reads
the table today, so dropping a DELETE leaves 24 tests green - the index is a
promise kept for a reader that does not exist yet.

This test reads the index the way that reader would. A row enters only when the
narrative is complete (:314), so the turn is finished with store.narrative().
"""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.storage import Store

TEXT = 'Трапезная пуста.'


def _turn(tmp_path):
    store = Store(tmp_path / 'index.sqlite3')
    cid = store.create('Поиск', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    store.record_scenario(cid, 'walk-1', 'Иду в трапезную',
                          lambda state: [dict(type='scripted', text=TEXT)])
    event = store.history(cid)[-1]['id']
    store.narrative(event, TEXT, 'complete')
    return store, cid, event


def _search(store, cid, word):
    with store.connection() as db:
        return [r[0] for r in db.execute(
            "SELECT event_id FROM journal_fts WHERE campaign=? AND journal_fts MATCH ?",
            (cid, word)).fetchall()]


def test_an_undone_turn_leaves_the_index(tmp_path):
    store, cid, event = _turn(tmp_path)
    assert _search(store, cid, 'Трапезная'), 'the index never held the entry'

    store.undo(cid)
    assert not _search(store, cid, 'Трапезная'), (
        'an undone turn still answers the search')


def test_a_rewritten_entry_is_indexed_once(tmp_path):
    store, cid, event = _turn(tmp_path)
    store.edit_narrative(event, 'Трапезная гудит голосами.')

    hits = _search(store, cid, 'Трапезная')
    assert hits == [event], 'the index holds %d rows for one entry' % len(hits)