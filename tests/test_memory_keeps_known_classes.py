"""Memory accepts only the classes it can reason about."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.memory import Memory
from solo.rulesets import BUILTINS
from solo.storage import Store


BASE = dict(campaign="c1", event="e1", kind="fact", provenance="scenario",
            truth='fact',
            scope='public', location='hall', text='Двор пуст', importance=1,
            minutes=0, recipients=())


def _put(memory, **extra):
    body = dict(BASE, key='k1')
    body.update(extra)
    return body


def test_an_unknown_truth_class_is_refused(tmp_path):
    memory = Memory(Store(tmp_path / 'mem.sqlite3'))
    with memory.store.connection(True) as db:
        with pytest.raises(ValueError) as caught:
            memory.put(db, **_put(memory, truth='легенда'))
    assert 'truth' in str(caught.value).lower(), caught.value


def test_an_unknown_scope_is_refused(tmp_path):
    memory = Memory(Store(tmp_path / 'mem2.sqlite3'))
    with memory.store.connection(True) as db:
        with pytest.raises(ValueError) as caught:
            memory.put(db, **_put(memory, scope='всем'))
    assert 'scope' in str(caught.value).lower(), caught.value


def test_an_unknown_provenance_is_refused(tmp_path):
    memory = Memory(Store(tmp_path / 'mem3.sqlite3'))
    with memory.store.connection(True) as db:
        with pytest.raises(ValueError) as caught:
            memory.put(db, **_put(memory, provenance='слух'))
    assert 'provenance' in str(caught.value).lower(), caught.value


def test_a_statement_cannot_become_world_truth(tmp_path):
    memory = Memory(Store(tmp_path / 'mem4.sqlite3'))
    with memory.store.connection(True) as db:
        with pytest.raises(ValueError) as caught:
            memory.put(db, **_put(memory, provenance='npc_statement',
                                  truth='fact'))
    assert 'statement' in str(caught.value).lower(), caught.value


def test_a_record_cannot_borrow_another_campaigns_entity(tmp_path):
    store = Store(tmp_path / 'mem5.sqlite3')
    memory = Memory(store)
    mine = store.create('Моя', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    other = store.create('Чужая', Hero(name='Чужак'), BUILTINS['dnd2024'])['id']
    with store.connection(True) as db:
        entity = memory.entity(db, other, 'npc', 'Чужак')
    with store.connection(True) as db:
        with pytest.raises(ValueError) as caught:
            memory.put(db, **_put(memory, recipients=(entity,),
                                  key='k-foreign', event='e-foreign',
                                  campaign=mine))
    assert 'campaign' in str(caught.value).lower(), caught.value

def test_a_record_cannot_cite_a_foreign_event(tmp_path):
    store = Store(tmp_path / "mem6.sqlite3")
    memory = Memory(store)
    mine = store.create("Моя", Hero(name="Герой"), BUILTINS["dnd2024"])["id"]
    other = store.create("Чужая", Hero(name="Чужак"), BUILTINS["dnd2024"])["id"]
    store.record_scenario(other, "walk-9", "Иду",
                          lambda state: [dict(type="scripted", text="Пусто.")])
    foreign = store.history(other)[-1]["id"]
    with store.connection(True) as db:
        with pytest.raises(ValueError) as caught:
            memory.put(db, **_put(memory, event=foreign, campaign=mine,
                                  key="k-foreign-event"))
    assert "event" in str(caught.value).lower(), caught.value


def test_a_record_cannot_cite_a_missing_event(tmp_path):
    store = Store(tmp_path / "mem7.sqlite3")
    memory = Memory(store)
    mine = store.create("Моя", Hero(name="Герой"), BUILTINS["dnd2024"])["id"]
    with store.connection(True) as db:
        with pytest.raises(ValueError) as caught:
            memory.put(db, **_put(memory, event="not-there", campaign=mine,
                                  key="k-missing"))
    assert "event" in str(caught.value).lower(), caught.value

def test_a_record_is_found_by_search(tmp_path):
    store = Store(tmp_path / "mem8.sqlite3")
    memory = Memory(store)
    mine = store.create("Поиск", Hero(name="Герой"), BUILTINS["dnd2024"])["id"]
    store.record_scenario(mine, "walk-1", "Иду",
                          lambda state: [dict(type="scripted", text="Пусто.")])
    event = store.history(mine)[-1]["id"]
    with store.connection(True) as db:
        rid = memory.put(db, **_put(memory, campaign=mine, event=event,
                                     key="k-search",
                                     text="Серебряный ключ лежит в нише"))
    with store.connection() as db:
        found = [r[0] for r in db.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?",
            ("ключ",)).fetchall()]
    assert rid in found, (
        "the record cannot be found by its own words: %r" % found)

def test_a_record_is_kept_under_its_subject(tmp_path):
    store = Store(tmp_path / "mem9.sqlite3")
    memory = Memory(store)
    mine = store.create("Субъект", Hero(name="Герой"), BUILTINS["dnd2024"])["id"]
    store.record_scenario(mine, "walk-1", "Иду",
                          lambda state: [dict(type="scripted", text="Пусто.")])
    event = store.history(mine)[-1]["id"]
    with store.connection(True) as db:
        subject = memory.entity(db, mine, "npc", "Кузнец")
        rid = memory.put(db, **_put(memory, campaign=mine, event=event,
                                     key="k-subject", subjects=(subject,),
                                     text="Кузнец хранит ключ"))
    with store.connection() as db:
        kept = [r[0] for r in db.execute(
            "SELECT entity_id FROM memory_subjects WHERE record_id=?",
            (rid,)).fetchall()]
    assert subject in kept, (
        "the record is not kept under its subject: %r" % kept)
