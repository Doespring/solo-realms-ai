"""Undoing a turn must also take it out of the journal search index.

solo/storage.py:555 deletes the event from journal_fts when a turn is undone.
Without that line the text of a cancelled turn stays searchable forever, and a
later recall can quote something the table already took back.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio

from scripts.command_smoke import FixedDice, setup


def _play(game, store, campaign, request_id, text):
    version = store.campaign(campaign)['version']
    return asyncio.run(game.play_command(campaign, request_id, text, version, rng=FixedDice()))


def _indexed(store, event):
    with store.connection(True) as c:
        return [r[0] for r in c.execute('SELECT event_id FROM journal_fts WHERE event_id=?', (event,))]


def test_undo_removes_the_turn_from_the_search_index(tmp_path):
    store, campaign, game = setup(tmp_path)
    result = _play(game, store, campaign, 'index-1', 'Исследую следы')
    assert result['status'] == 'resolved', result
    event = result['journal_id']
    store.narrative(event, 'На полу видны следы.', 'complete')
    assert _indexed(store, event), 'a finished narrative is searchable'

    store.undo(campaign)

    assert _indexed(store, event) == [], 'an undone turn must leave the index'
