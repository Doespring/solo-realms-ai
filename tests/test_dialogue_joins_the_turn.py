"""One turn must be one exchange: state, journal event and dialogue row together.

The social path wrote the dialogue with store.converse, which opens its own
write transaction. That call sat inside the caller's open write transaction, so
SQLite refused the second BEGIN IMMEDIATE and the memory step failed with
'database is locked'. The caller caught that and told the player 'Сцена
изменилась' - a story excuse for a technical failure - while the journal event
still stood without its dialogue row.

These tests take the failure from the side that matters: after the whole turn,
state, event and dialogue must agree, and a failed turn must leave no half of
the pair behind.
"""
import os
os.environ['SOLO_TEST']='1'

import sqlite3
import tempfile
import pathlib

from solo.storage import Store


def store_at(tmp_path):
    store = Store(tmp_path/'social.sqlite3')
    with store.connection(True) as c:
        c.execute("INSERT INTO campaigns(id,name,state,master,version,created) VALUES(?,?,?,?,?,?)",
                  ('c1', 'Сцена', '{}', '{}', 1, 1000.0))
    return store


def dialogue(path):
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT request_id,kind FROM conversations WHERE campaign='c1'").fetchall()
    finally:
        db.close()


def test_a_dialogue_row_joins_the_caller_transaction(tmp_path):
    store = store_at(tmp_path)
    path = str(tmp_path/'social.sqlite3')
    with store.connection(True) as c:
        store.converse('c1', 'r1', 'Привет', 'Здравствуй', 'notice', {}, db=c)
        # Inside the transaction the row must already be visible on that
        # connection - no second connection, no lock wait.
        assert c.execute("SELECT COUNT(*) FROM conversations WHERE request_id='r1'").fetchone()[0] == 1
    assert dialogue(path) == [('r1', 'notice')]


def test_a_failed_turn_leaves_no_half_of_the_pair(tmp_path):
    store = store_at(tmp_path)
    path = str(tmp_path/'social.sqlite3')
    try:
        with store.connection(True) as c:
            store.converse('c1', 'r2', 'Повтор', 'Ответ', 'notice', {}, db=c)
            raise RuntimeError('сбой после записи реплики')
    except RuntimeError:
        pass
    assert dialogue(path) == [], 'a rollback must not leave one half of the pair'


def test_the_same_request_twice_gives_one_row(tmp_path):
    store = store_at(tmp_path)
    path = str(tmp_path/'social.sqlite3')
    with store.connection(True) as c:
        first = store.converse('c1', 'r3', 'Привет', 'Здравствуй', 'notice', {}, db=c)
    again = store.converse('c1', 'r3', 'Привет', 'Здравствуй', 'notice', {})
    assert again['id'] == first['id']
    assert len(dialogue(path)) == 1


def test_a_different_text_under_one_request_is_refused(tmp_path):
    store = store_at(tmp_path)
    with store.connection(True) as c:
        store.converse('c1', 'r4', 'Привет', 'Здравствуй', 'notice', {}, db=c)
    try:
        store.converse('c1', 'r4', 'Уходи', 'Здравствуй', 'notice', {})
    except ValueError as error:
        assert 'другой текст' in str(error)
    else:
        raise AssertionError('the row would silently change its words')
