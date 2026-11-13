import asyncio,copy,json
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app,PlayRequest
from solo.contracts import Hero
from solo.storage import Store
from solo.inventory import InventoryEngine
from solo.inventory_service import InventoryService,InventoryCommand,MerchantSetup,TradeDraft,TradeApproval
from solo.combat_service import CombatService,StartEncounter,CombatCommand
from solo.rulesets import BUILTINS
from solo.game import Game
from test_rulesets import Dice

def setup(tmp_path,*,hero=None,edition='dnd2024'):
    store=Store(tmp_path/'items.sqlite3');cid=store.create('Торговля',hero or Hero(name='Герой'),BUILTINS[edition])['id'];service=InventoryService(store)
    service.execute(cid,InventoryCommand(request_id='wallet',kind='wallet',confirm=True,wallet_cp=10000,source='Подтверждённое начальное богатство'))
    event=service.merchant(cid,MerchantSetup(request_id='merchant',confirm=True,name='Ирина',stock=[dict(catalog='healing_potion',quantity=2),dict(catalog='rope',quantity=3)]))
    mid=event['results'][0]['merchant']
    return store,cid,service,mid

def grant(service,cid,catalog,qty=1):
    return service.execute(cid,InventoryCommand(request_id='grant-'+catalog,kind='grant',catalog=catalog,quantity=qty,confirm=True,source='Тестовый подтверждённый источник'))['results'][0]['item']

def test_buy_quote_requires_confirmation_then_atomic_replay_undo_restart(tmp_path):
    store,cid,service,mid=setup(tmp_path);before=copy.deepcopy(store.campaign(cid)['state']);engine=InventoryEngine(before)
    potion=next(i for i in engine.data.items if i.catalog=='healing_potion')
    draft=TradeDraft(request_id='quote',merchant=mid,take=[dict(item=potion.id,quantity=1)])
    quote=service.quote(cid,draft)
    assert quote['status']=='ready' and quote['terms']['pay_cp']==5000 and store.campaign(cid)['state']==before
    assert service.quote(cid,draft)['id']==quote['id']
    approval=TradeApproval(request_id='buy',offer=quote['id'],confirm=True)
    with pytest.raises(ValueError):service.apply(cid,approval.model_copy(update={'confirm':False}))
    event=service.apply(cid,approval)
    assert service.apply(cid,approval)['id']==event['id']
    after=InventoryEngine(store.campaign(cid)['state'])
    assert after.data.wallet_cp==5000 and after.item(potion.id,'merchant:'+mid).quantity==1
    assert sum(i.quantity for i in after.data.items if i.catalog=='healing_potion' and i.owner=='hero')==1
    assert Store(store.path).campaign(cid)['state']==store.campaign(cid)['state']
    store.undo(cid);assert store.campaign(cid)['state']==before
    with pytest.raises(ValueError):service.apply(cid,approval)

def test_failed_commit_rolls_back_money_items_and_offer_status(tmp_path):
    store,cid,service,mid=setup(tmp_path);state=copy.deepcopy(store.campaign(cid)['state']);rope=next(i for i in InventoryEngine(state).data.items if i.catalog=='rope')
    quote=service.quote(cid,TradeDraft(request_id='quote',merchant=mid,take=[dict(item=rope.id)]))
    original=store.memory.ingest
    def fail(*args):raise RuntimeError('Simulated journal failure')
    store.memory.ingest=fail
    with pytest.raises(RuntimeError):service.apply(cid,TradeApproval(request_id='buy',offer=quote['id'],confirm=True))
    assert store.campaign(cid)['state']==state
    with store.connection() as c:assert c.execute('SELECT status FROM trade_offers WHERE id=?',(quote['id'],)).fetchone()[0]=='ready'
    store.memory.ingest=original
    service.apply(cid,TradeApproval(request_id='buy',offer=quote['id'],confirm=True))

def test_stale_offer_does_not_move_money_and_duplicate_lines_rejected(tmp_path):
    store,cid,service,mid=setup(tmp_path);item=next(i for i in InventoryEngine(store.campaign(cid)['state']).data.items if i.catalog=='rope')
    with pytest.raises(ValueError):TradeDraft(request_id='bad',merchant=mid,take=[dict(item=item.id),dict(item=item.id)])
    quote=service.quote(cid,TradeDraft(request_id='quote',merchant=mid,take=[dict(item=item.id)]))
    grant(service,cid,'club');before=copy.deepcopy(store.campaign(cid)['state'])
    with pytest.raises(ValueError,match='изменилось'):service.apply(cid,TradeApproval(request_id='buy',offer=quote['id'],confirm=True))
    assert store.campaign(cid)['state']==before

def test_sell_and_barter_use_prices_and_explicit_counteroffer(tmp_path):
    store,cid,service,mid=setup(tmp_path);club=grant(service,cid,'club')
    sell=service.quote(cid,TradeDraft(request_id='sell-quote',kind='sell',merchant=mid,give=[dict(item=club)]))
    assert sell['terms']['receive_cp']==5
    service.apply(cid,TradeApproval(request_id='sell',offer=sell['id'],confirm=True))
    sword=grant(service,cid,'longsword');rope=next(i for i in InventoryEngine(store.campaign(cid)['state']).data.items if i.owner=='merchant:'+mid and i.catalog=='rope')
    offer=service.quote(cid,TradeDraft(request_id='barter',kind='barter',merchant=mid,give=[dict(item=sword)],take=[dict(item=rope.id)]))
    assert offer['status']=='counter' and offer['terms']['receive_cp']==650 and offer['terms']['pay_cp']==0
    service.apply(cid,TradeApproval(request_id='exchange',offer=offer['id'],confirm=True))
    after=InventoryEngine(store.campaign(cid)['state'])
    assert after.data.wallet_cp==10655 and any(i.catalog=='rope' and i.owner=='hero' for i in after.data.items)

def test_merchant_refusal_never_creates_free_items(tmp_path):
    store,cid,service,mid=setup(tmp_path);club=grant(service,cid,'club')
    def hostile(state):
        state['economy']['merchants'][0]['relationship']=-100
        return [dict(type='scripted',text='Подтверждённое изменение отношений')]
    store.record_scenario(cid,'hostile','Отношения',hostile)
    before=copy.deepcopy(store.campaign(cid)['state'])
    offer=service.quote(cid,TradeDraft(request_id='refuse',kind='sell',merchant=mid,give=[dict(item=club)]))
    assert offer['status']=='refused'
    with pytest.raises(ValueError):service.apply(cid,TradeApproval(request_id='sale',offer=offer['id'],confirm=True))
    assert store.campaign(cid)['state']==before

def test_receive_capacity_and_custom_prose_has_no_effect(tmp_path):
    store,cid,service,mid=setup(tmp_path);before=copy.deepcopy(store.campaign(cid))
    with pytest.raises(ValueError,match='грузоподъёмность'):grant(service,cid,'chain_mail',10)
    assert store.campaign(cid)==before
    event=service.execute(cid,InventoryCommand(request_id='manual',kind='grant',confirm=True,source='Игрок подтвердил сувенир',manual=dict(id='discard',name='Камень бессмертия',properties=['Воскрешает всех'],parameters={'python':"__import__('os').remove('file')"},owner='hero',source='Сувенир')))
    id=event['results'][0]['item']
    with pytest.raises(ValueError,match='не зарегистрировано'):service.execute(cid,InventoryCommand(request_id='magic',kind='use',item=id))
    assert store.campaign(cid)['state']['hero']['hp']==12

def test_equipment_ac_hands_and_unequip_restore_manual_base(tmp_path):
    hero=Hero(name='Герой',armor_proficiencies=['light','shields'])
    store,cid,service,mid=setup(tmp_path,hero=hero)
    armor=grant(service,cid,'leather');shield=grant(service,cid,'shield');bow=grant(service,cid,'shortbow')
    service.execute(cid,InventoryCommand(request_id='armor',kind='equip',item=armor,slot='armor'))
    service.execute(cid,InventoryCommand(request_id='shield',kind='equip',item=shield,slot='shield'))
    assert store.campaign(cid)['state']['hero']['ac']==15
    with pytest.raises(ValueError,match='несовместим'):service.execute(cid,InventoryCommand(request_id='bow',kind='equip',item=bow))
    service.execute(cid,InventoryCommand(request_id='unarmor',kind='unequip',slot='armor'));assert store.campaign(cid)['state']['hero']['ac']==16
    service.execute(cid,InventoryCommand(request_id='unshield',kind='unequip',slot='shield'));assert store.campaign(cid)['state']['hero']['ac']==14

@pytest.mark.parametrize('edition,economy',[('dnd2014','action'),('dnd2024','bonus_action')])
def test_potion_edition_action_and_roll_replay(tmp_path,edition,economy):
    store,cid,service,mid=setup(tmp_path,hero=Hero(name='Герой',hp=3),edition=edition);potion=grant(service,cid,'healing_potion',2)
    CombatService(store,rng=Dice(20,1)).start(cid,StartEncounter(request_id='start',confirm=True))
    service.rng=Dice(2,3);body=InventoryCommand(request_id='potion',kind='use',item=potion)
    event=service.execute(cid,body)
    assert service.execute(cid,body)['id']==event['id'] and service.rng.calls==2
    state=store.campaign(cid)['state'];actor=state['combat']['actors'][0]
    assert state['hero']['hp']==10 and not actor[economy]
    assert InventoryEngine(state).item(potion).quantity==1

def test_equipped_bow_uses_real_arrow_once_and_cannot_free_override(tmp_path):
    store,cid,service,mid=setup(tmp_path);bow=grant(service,cid,'shortbow');arrows=grant(service,cid,'arrows',1)
    service.execute(cid,InventoryCommand(request_id='equip',kind='equip',item=bow))
    combat=CombatService(store,rng=Dice(20,1,1));combat.start(cid,StartEncounter(request_id='start',confirm=True,hero_weapon='longsword'))
    assert store.campaign(cid)['state']['combat']['actors'][0]['weapon']=='shortbow'
    body=CombatCommand(request_id='shot',kind='attack',target='foe-1');event=combat.execute(cid,body)
    assert not event['results'][0]['operations'][0]['hit']
    assert combat.execute(cid,body)['id']==event['id']
    assert next(i for i in store.campaign(cid)['state']['economy']['items'] if i['id']==arrows)['quantity']==0
    store.undo(cid);assert InventoryEngine(store.campaign(cid)['state']).item(arrows).quantity==1

def test_gift_npc_inventory_and_cash_are_not_sent_to_client(tmp_path):
    store,cid,service,mid=setup(tmp_path);rope=grant(service,cid,'rope')
    service.execute(cid,InventoryCommand(request_id='gift',kind='give',item=rope,recipient='Ирина'))
    # r210: кампания прежней версии не открывается по HTTP, поэтому тот же
    # публичный проектор вызывается напрямую — проверяется форма ответа.
    from solo.combat_view import public_campaign
    payload=public_campaign(store.campaign(cid))['state']['economy']
    assert rope not in [i['id'] for i in payload['items']]
    assert 'money_cp' not in json.dumps(payload['merchants']) and 'npc:' not in json.dumps(payload)

def test_concurrent_acceptance_transfers_only_once(tmp_path):
    store,cid,service,mid=setup(tmp_path);rope=next(i for i in InventoryEngine(store.campaign(cid)['state']).data.items if i.catalog=='rope')
    quote=service.quote(cid,TradeDraft(request_id='quote',merchant=mid,take=[dict(item=rope.id)]))
    def accept(id):
        try:return service.apply(cid,TradeApproval(request_id=id,offer=quote['id'],confirm=True))['id']
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(accept,['one','two']))
    assert sum(x is not None for x in results)==1
    state=InventoryEngine(store.campaign(cid)['state'])
    assert state.data.wallet_cp==9900 and sum(i.quantity for i in state.data.items if i.catalog=='rope' and i.owner=='hero')==1

def test_exact_text_inventory_uses_no_interpreter_and_negation_never_consumes(tmp_path):
    store,cid,service,mid=setup(tmp_path);id=grant(service,cid,'longsword')
    class NoAI:
        metrics=[]
        async def interpret(self,*args):raise AssertionError('Should use exact command')
    game=Game(store,NoAI())
    result=asyncio.run(game.play(cid,PlayRequest(request_id='equip-text',text='Экипирую длинный меч')))
    assert result['kind']=='event' and InventoryEngine(store.campaign(cid)['state']).data.equipment['weapon']==id
    from solo.inventory_api import text_command
    assert text_command('Не использую лечебное зелье',store.campaign(cid)['state'],'no') is None
    quote=text_command('Покупаю 2 верёвки у Ирины',store.campaign(cid)['state'],'quote-text')
    assert quote.merchant==mid and quote.take[0].quantity==2
