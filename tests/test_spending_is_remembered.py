"""Spending money must leave a memory row, not just a smaller number.

solo/memory.py:234 records a 'resource' entry whenever economy.wallet_cp moves.
Replacing that condition with 'if False' left 42 tests green: none of them spends
money through a command and then looks for the record. Section 11 asks that the
journal stay truthful about the hero's purse.
"""
import os
os.environ['SOLO_TEST']='1'

from pathlib import Path

from solo.inventory_service import InventoryCommand, InventoryService
from solo.rulesets import BUILTINS
from solo.storage import Store
from solo.contracts import Hero


def _campaign(tmp_path, wallet):
    store = Store(Path(tmp_path) / 'purse.sqlite3')
    cid = store.create('Кошелёк', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    InventoryService(store).execute(cid, InventoryCommand(
        request_id='wallet-1', kind='wallet', confirm=True, wallet_cp=wallet,
        source='Подтверждено игроком'))
    return store, cid


def _rows(store, cid):
    with store.connection(False) as db:
        return db.execute('SELECT record_key, content FROM memory_records WHERE campaign=?',
                          (cid,)).fetchall()


def test_a_wallet_change_is_remembered(tmp_path):
    store, cid = _campaign(tmp_path, 500)
    rows = _rows(store, cid)
    keys = [str(r[0]) for r in rows]
    assert any(k.endswith(':wallet') for k in keys), 'no wallet memory row: ' + str(keys)


def test_the_memory_names_both_amounts(tmp_path):
    store, cid = _campaign(tmp_path, 500)
    texts = [str(r[1]) for r in _rows(store, cid) if str(r[0]).endswith(':wallet')]
    assert texts, 'no wallet row'
    assert '500' in ''.join(texts), ''.join(texts)[:200]
