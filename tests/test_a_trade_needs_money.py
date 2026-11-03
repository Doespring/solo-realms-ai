"""A trade must not complete when the hero cannot pay.

solo/inventory_service.py:72 refuses a deal whose price exceeds the wallet, and
whose payout exceeds the merchant's money. Removing that line left
test_inventory.py and test_inventory_http.py green: they exercise transfers, not
a purchase the hero cannot afford. Section 11 asks that money never be created
or destroyed by a rejected action.
"""
import os
os.environ['SOLO_TEST']='1'

import copy

import pytest

from solo.inventory import InventoryEngine, Merchant
from solo.inventory_service import apply_terms


def _shop(state):
    state = copy.deepcopy(state)
    state.setdefault('economy', {})
    engine = InventoryEngine(state)
    engine.data.wallet_cp = 50
    place = state['location']
    spot = state['locations'][[p['name'] for p in state['locations']].index(place)]
    if not spot['npc']:
        spot['npc'].append(dict(name='Торговец', description='Торговец.', facts=[]))
    npc = spot['npc'][0]['name']
    engine.data.merchants.append(Merchant(id='m1', name=npc, location=place, available=True, money_cp=10))
    return engine


def _terms(pay):
    return dict(merchant='m1', pay_cp=pay, receive_cp=0, give=[], take=[])


def test_a_purchase_beyond_the_wallet_is_refused(tmp_path):
    from scripts.command_smoke import setup
    store, campaign, game = setup(tmp_path)
    engine = _shop(store.campaign(campaign)['state'])
    before = engine.data.wallet_cp
    with pytest.raises(ValueError) as refused:
        apply_terms(engine, _terms(before + 1))
    assert 'Недостаточно монет' in str(refused.value), str(refused.value)


def test_a_refused_purchase_leaves_the_wallet_alone(tmp_path):
    from scripts.command_smoke import setup
    store, campaign, game = setup(tmp_path)
    engine = _shop(store.campaign(campaign)['state'])
    before = engine.data.wallet_cp
    with pytest.raises(ValueError):
        apply_terms(engine, _terms(before + 1))
    assert engine.data.wallet_cp == before, 'money was moved by a refused deal'
