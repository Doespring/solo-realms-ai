"""Every turn the player made must stay readable, not just the newest fifty.

The audit (reports/INDEPENDENT_PROJECT_AUDIT-2026-10-23.md, section 10) found 56
rows in conversations and 50 served, with no cursor to ask for the rest: the
oldest six were unreachable although nothing had been deleted. The reader now
takes a stable cursor and pages backwards without gaps or repeats.
"""
import os
os.environ['SOLO_TEST']='1'

import pathlib
import sqlite3

from solo.storage import Store


def _campaign(tmp_path, turns):
    path = tmp_path / 'solo.sqlite3'
    store = Store(path)
    cid = store.create('Переписка', __import__('solo.contracts', fromlist=['Hero']).Hero(name='Мирон', hp=12, max_hp=12))['id']
    with store.connection() as c:
        for i in range(turns):
            c.execute('INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) '
                      'VALUES (?,?,?,?,?,?,?,?)',
                      ('c%03d' % i, cid, 'r%d' % i, 'ход %d' % i, 'ответ %d' % i, 'notice', '{}',
                       # Deliberately shared timestamps: a float cursor would
                       # tie here and skip or repeat a row at the boundary.
                       1000.0 + (i // 4)))
    return store, cid


def _walk(store, cid, size):
    """Read the whole dialogue backwards, exactly as the UI would."""
    page = store.conversation_history(cid, limit=size)
    seen = [row['id'] for row in page]
    while True:
        cursor = store.conversation_cursor(page)
        if cursor is None:
            break
        page = store.conversation_history(cid, limit=size, before=cursor)
        if not page:
            break
        seen = [row['id'] for row in page] + seen
    return seen


def test_all_fifty_six_turns_are_reachable(tmp_path):
    store, cid = _campaign(tmp_path, 56)
    assert store.conversation_count(cid) == 56
    first = store.conversation_history(cid)
    assert len(first) == 50, 'the newest page is still fifty rows'
    assert first[0]['original'] == 'ход 6', 'and it no longer starts at the beginning'


def test_paging_covers_every_turn_once(tmp_path):
    store, cid = _campaign(tmp_path, 56)
    for size in (1, 3, 7, 50, 100):
        seen = _walk(store, cid, size)
        assert len(seen) == len(set(seen)), 'page boundary repeated a row'
        assert seen == ['c%03d' % i for i in range(56)], 'paging skipped or reordered rows'


def test_the_cursor_does_not_shift_when_new_turns_arrive(tmp_path):
    store, cid = _campaign(tmp_path, 56)
    cursor = store.conversation_cursor(store.conversation_history(cid))
    before = store.conversation_history(cid, limit=50, before=cursor)
    with store.connection() as c:
        c.execute('INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) '
                  'VALUES (?,?,?,?,?,?,?,?)', ('cnew', cid, 'rnew', 'новый ход', 'новый ответ', 'notice', '{}', 2000.0))
    after = store.conversation_history(cid, limit=50, before=cursor)
    assert [r['id'] for r in before] == [r['id'] for r in after], 'a new turn moved an older page'


def test_cancelled_turn_keeps_its_place_in_the_reading_order(tmp_path):
    # Ordering is time, not status: a cancelled branch must not vanish from the
    # record, or the player's own words disappear from their own log.
    store, cid = _campaign(tmp_path, 6)
    with store.connection() as c:
        c.execute("UPDATE conversations SET status='undone' WHERE id='c002'")
    seen = _walk(store, cid, 2)
    assert 'c002' in seen
    assert len(seen) == 6
