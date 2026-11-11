"""Memory from an undone turn must not come back.

solo/memory.py:269 filters the recall query with 'e.undone=0'. Replacing that
clause with a tautology left four tests green: they read memory_records
directly, which still holds the row - only the recall path hides it.

A player who undoes a turn has undone its consequences; the journal must not
keep answering as if the purchase happened. Section 11.
"""
import os
os.environ['SOLO_TEST']='1'

from pathlib import Path

from solo.contracts import Hero
from solo.inventory_service import InventoryCommand, InventoryService
from solo.memory import Memory
from solo.rulesets import BUILTINS
from solo.storage import Store


def _campaign(tmp_path):
    store = Store(Path(tmp_path) / 'undone-memory.sqlite3')
    cid = store.create('Память', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    InventoryService(store).execute(cid, InventoryCommand(
        request_id='wallet-1', kind='wallet', confirm=True, wallet_cp=500,
        source='Подтверждено игроком'))
    return store, cid


def _hero_identity(store, cid):
    with store.connection(False) as db:
        row = db.execute("SELECT id FROM memory_entities WHERE campaign=? AND kind='hero'",
                         (cid,)).fetchone()
    return row['id'] if row else None


def _wallet_texts(store, cid):
    hero = _hero_identity(store, cid)
    assert hero, 'the campaign has no hero memory identity'
    memory = Memory(store)
    with store.connection(False) as db:
        found = memory.retrieve(cid, audience=[hero], query='кошелёк', location='')
    return [item for item in found['records'] if 'wallet' in str(item).lower()]


def test_committed_memory_is_visible(tmp_path):
    store, cid = _campaign(tmp_path)
    assert _wallet_texts(store, cid), 'the wallet fact is not visible at all'


def test_undone_memory_is_hidden(tmp_path):
    store, cid = _campaign(tmp_path)
    store.undo(cid)
    assert not _wallet_texts(store, cid), 'an undone turn still answers'

def _record_id(store, cid):
    with store.connection(False) as db:
        row = db.execute(
            "SELECT m.id FROM memory_records m JOIN memory_access a ON a.record_id=m.id"
            " WHERE m.campaign=? LIMIT 1", (cid,)).fetchone()
    assert row, 'the campaign holds no accessible memory record'
    return row['id']


def test_an_undone_memory_is_not_known(tmp_path):
    """memory.knows() is a second read path; the recall filter must not cover it."""
    store, cid = _campaign(tmp_path)
    hero = _hero_identity(store, cid)
    record = _record_id(store, cid)
    memory = Memory(store)
    assert memory.knows(cid, hero, record) is True, 'the fact is unknown at all'

    store.undo(cid)

    assert memory.knows(cid, hero, record) is False, (
        'an undone fact is still reported as known')
