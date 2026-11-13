"""Atomic inventory mutations and explicit, version-bound merchant offers."""
import copy,json,time,uuid
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Strict,Hero
from .inventory import InventoryEngine,Item,Merchant,CATALOG,new_item
from .rulesets import digest
from .storage import dump

class InventoryCommand(Strict):
    request_id: str = Field(min_length=1,max_length=80)
    expected_version: int | None = Field(default=None,ge=0)
    kind: Literal['grant','wallet','equip','unequip','use','discard','give']
    item: str = Field(default='',max_length=80)
    catalog: str = Field(default='',max_length=80)
    quantity: int = Field(default=1,ge=1,le=10000)
    slot: Literal['weapon','armor','shield'] = 'weapon'
    recipient: str = Field(default='',max_length=120)
    source: str = Field(default='',max_length=800)
    wallet_cp: int = Field(default=0,ge=0,le=1000000000)
    confirm: bool = False
    manual: Item | None = None

class Stock(Strict):
    catalog: str = Field(min_length=1,max_length=80)
    quantity: int = Field(default=1,ge=1,le=1000)
class MerchantSetup(Strict):
    request_id: str = Field(min_length=1,max_length=80)
    expected_version: int | None = Field(default=None,ge=0)
    confirm: bool = False
    name: str = Field(min_length=1,max_length=80)
    money_cp: int = Field(default=10000,ge=0,le=1000000000)
    buy_percent: int = Field(default=50,ge=0,le=100)
    sell_percent: int = Field(default=100,ge=1,le=300)
    relationship: int = Field(default=0,ge=-100,le=100)
    refuse_below: int = Field(default=-50,ge=-100,le=100)
    accepts: list[Literal['weapon','armor','consumable','ammunition','gear']] = Field(default_factory=lambda:['weapon','armor','consumable','ammunition','gear'],max_length=5)
    stock: list[Stock] = Field(default_factory=list,max_length=100)

class Line(Strict):
    item: str = Field(min_length=1,max_length=80)
    quantity: int = Field(default=1,ge=1,le=10000)
class TradeDraft(Strict):
    request_id: str = Field(min_length=1,max_length=80)
    expected_version: int | None = Field(default=None,ge=0)
    merchant: str = Field(min_length=1,max_length=80)
    kind: Literal['buy','sell','barter'] = 'buy'
    give: list[Line] = Field(default_factory=list,max_length=30)
    take: list[Line] = Field(default_factory=list,max_length=30)
    pay_cp: int = Field(default=0,ge=0,le=1000000000)
    receive_cp: int = Field(default=0,ge=0,le=1000000000)
    @model_validator(mode='after')
    def valid(self):
        ids=[r.item for r in self.give+self.take]
        if len(set(ids))!=len(ids):raise ValueError('Повтор предмета в предложении')
        if not ids:raise ValueError('Укажите хотя бы один предмет')
        if self.kind=='buy' and (self.give or self.pay_cp or self.receive_cp):raise ValueError('Покупка содержит только список покупаемых предметов; цену вычисляет торговец')
        if self.kind=='sell' and (self.take or self.pay_cp or self.receive_cp):raise ValueError('Продажа содержит только список продаваемых предметов')
        return self

class TradeApproval(Strict):
    request_id: str = Field(min_length=1,max_length=80)
    offer: str = Field(min_length=1,max_length=80)
    confirm: bool = False

def outside_combat(state):
    if state.get('combat',{}).get('active'):raise ValueError('Торговля и ручной ввод снаряжения доступны вне боя')
    if state.get('hero_death') or state['hero']['hp']==0:raise ValueError('Герой сейчас не может совершить эту операцию')

def apply_terms(engine,terms):
    merchant=engine.available_merchant(terms['merchant']);owner='merchant:'+merchant.id
    if terms['pay_cp']>engine.data.wallet_cp or terms['receive_cp']>merchant.money_cp+terms['pay_cp']:raise ValueError('Недостаточно монет для этой сделки')
    received=[]
    for line in terms['give']:engine.transfer(line['item'],line['quantity'],owner,source='Сделка с '+merchant.name)
    for line in terms['take']:received.append(engine.transfer(line['item'],line['quantity'],'hero',owner=owner,source='Покупка у '+merchant.name).name)
    engine.data.wallet_cp+=terms['receive_cp']-terms['pay_cp'];merchant.money_cp+=terms['pay_cp']-terms['receive_cp']
    engine.assert_capacity();engine.flush()
    return received

class InventoryService:
    def __init__(self,store,*,rng=None):self.store=store;self.rng=rng
    def execute(self,campaign,body,*,original=None):
        with self.store.connection(True) as c:
            current=self.store.campaign(campaign,c)
            def transition(state):
                if body.expected_version is not None and body.expected_version!=current['version']:raise ValueError('Сцена изменилась; обновите команду')
                if body.kind!='use':outside_combat(state)
                engine=InventoryEngine(state,rng=self.rng);result={}
                if body.kind=='grant':
                    if not body.confirm or not body.source.strip():raise ValueError('Подтвердите ручное получение и укажите источник')
                    if body.manual:
                        if body.manual.catalog:raise ValueError('Ручной предмет не может выдавать себя за зарегистрированный')
                        item=Item.model_validate({**body.manual.model_dump(),'id':str(uuid.uuid4()),'owner':'hero','quantity':body.quantity,'source':body.source})
                    else:item=new_item(body.catalog,body.quantity,'hero',body.source)
                    engine.data.items.append(item);result=dict(text=f'Получено: {item.name} × {item.quantity}. Источник подтверждён игроком.',item=item.id)
                elif body.kind=='wallet':
                    if not body.confirm or not body.source.strip():raise ValueError('Подтвердите ручное изменение кошелька и источник')
                    engine.data.wallet_cp=body.wallet_cp;result=dict(text='Кошелёк изменён вручную игроком: '+str(body.wallet_cp)+' CP эквивалента. Источник: '+body.source)
                elif body.kind=='equip':result=dict(text=engine.equip(body.item,body.slot))
                elif body.kind=='unequip':result=dict(text=engine.unequip(body.slot))
                elif body.kind=='use':result=engine.use(body.item)
                elif body.kind=='discard':
                    item=engine.item(body.item)
                    if body.item in engine.data.equipment.values():raise ValueError('Сначала снимите предмет')
                    if body.quantity>item.quantity:raise ValueError('Недостаточно предметов')
                    item.quantity-=body.quantity;result=dict(text=f'Оставлено: {item.name} × {body.quantity}. Доступен откат.')
                elif body.kind=='give':
                    npc=next((n for p in state['locations'] if p['name']==state['location'] for n in p['npc'] if n.get('present',True) and n['name'].lower()==body.recipient.lower()),None)
                    if not npc:raise ValueError('Адресат отсутствует в сцене')
                    owner='npc:'+digest([state['location'],npc['name']])[:24]
                    item=engine.transfer(body.item,body.quantity,owner,source='Передано '+npc['name'])
                    result=dict(text=f'Передано: {item.name} × {body.quantity}; получатель {npc["name"]}.')
                if body.kind in ('grant','wallet'):engine.assert_capacity()
                engine.flush()
                return [dict(type='scripted',kind='inventory',**result)]
            signature=digest(body.model_dump(exclude={'request_id'}))
            return self.store.engine_event(c,current,body.request_id,original or 'Инвентарь: '+body.kind,transition,signature)
    def merchant(self,campaign,body,*,original=None):
        if not body.confirm:raise ValueError('Подтвердите добавление торговца и его запасов в канон')
        with self.store.connection(True) as c:
            current=self.store.campaign(campaign,c)
            def transition(state):
                outside_combat(state)
                if body.expected_version is not None and body.expected_version!=current['version']:raise ValueError('Сцена изменилась')
                engine=InventoryEngine(state)
                if any(m.name.lower()==body.name.lower() and m.location==state['location'] for m in engine.data.merchants):raise ValueError('Торговец уже добавлен в этой локации')
                merchant=Merchant(id=str(uuid.uuid4()),location=state['location'],**body.model_dump(include={'name','money_cp','buy_percent','sell_percent','relationship','refuse_below','accepts'}))
                place=next(p for p in state['locations'] if p['name']==state['location'])
                existing_npc=next((n for n in place['npc'] if n['name'].lower()==merchant.name.lower()),None)
                if existing_npc:merchant.name=existing_npc['name']
                engine.data.merchants.append(merchant)
                for line in body.stock:engine.data.items.append(new_item(line.catalog,line.quantity,'merchant:'+merchant.id,'Запас торговца подтверждён игроком'))
                if not any(n['name']==merchant.name for n in place['npc']):place['npc'].append(dict(name=merchant.name,character='Торговец, добавленный игроком.',known=''))
                engine.flush()
                return [dict(type='scripted',kind='merchant_setup',text='Торговец '+merchant.name+' и ассортимент подтверждены игроком.',merchant=merchant.id)]
            return self.store.engine_event(c,current,body.request_id,original or 'Добавление торговца',transition,digest(body.model_dump(exclude={'request_id'})))
    def quote(self,campaign,body):
        signature=digest(body.model_dump(exclude={'request_id'}))
        with self.store.connection(True) as c:
            current=self.store.campaign(campaign,c)
            old=c.execute('SELECT * FROM trade_offers WHERE campaign=? AND request_id=?',(campaign,body.request_id)).fetchone()
            if old:
                payload=json.loads(old['payload'])
                if payload['signature']!=signature:raise ValueError('request_id относится к другому предложению')
                return {**payload['public'],'status':old['status']}
            outside_combat(current['state'])
            if body.expected_version is not None and body.expected_version!=current['version']:raise ValueError('Сцена изменилась')
            engine=InventoryEngine(current['state']);merchant=engine.available_merchant(body.merchant)
            give=[(engine.item(line.item),line.quantity) for line in body.give];take=[(engine.item(line.item,'merchant:'+merchant.id),line.quantity) for line in body.take]
            if any(q>i.quantity for i,q in give+take):raise ValueError('Недостаточно запасов')
            if any(i.id in engine.data.equipment.values() for i,_ in give):raise ValueError('Сначала снимите продаваемое снаряжение')
            give_value=sum(engine.price(merchant,i,buying_from_hero=True)*q for i,q in give);take_value=sum(engine.price(merchant,i)*q for i,q in take)
            terms=dict(merchant=merchant.id,give=[l.model_dump() for l in body.give],take=[l.model_dump() for l in body.take],pay_cp=body.pay_cp,receive_cp=body.receive_cp)
            status='ready';text='Торговец принимает предложение. Сделка ждёт подтверждения.'
            if body.kind=='buy':terms['pay_cp']=take_value
            elif body.kind=='sell':terms['receive_cp']=give_value
            else:
                difference=take_value+terms['receive_cp']-give_value-terms['pay_cp']
                if difference>0:terms['pay_cp']+=difference
                elif difference<0:terms['receive_cp']-=difference
                if difference:status='counter';text='Торговец предлагает встречные условия; проверьте доплату/возврат и подтвердите отдельно.'
            if merchant.relationship<merchant.refuse_below or any(i.type not in merchant.accepts or i.condition=='broken' for i,_ in give):
                status='refused';text='Торговец отказывается от этой сделки по текущим отношениям или политике ассортимента.'
            if status!='refused':
                try:apply_terms(InventoryEngine(copy.deepcopy(current['state'])),terms)
                except ValueError as exc:status='refused';text='Сделка не может быть выполнена: '+str(exc)
            token=str(uuid.uuid4());expires=time.time()+600
            public=dict(id=token,status=status,merchant=merchant.name,terms=terms,items_given=[dict(name=i.name,quantity=q,price_cp=engine.price(merchant,i,buying_from_hero=True)) for i,q in give],items_received=[dict(name=i.name,quantity=q,price_cp=engine.price(merchant,i)) for i,q in take],text=text,expires=expires)
            payload=dict(signature=signature,base_version=current['version'],state_hash=digest(current['state']),public=public)
            c.execute('INSERT INTO trade_offers VALUES(?,?,?,?,?,?,?)',(token,campaign,body.request_id,dump(payload),status,expires,time.time()))
            return public
    def apply(self,campaign,body,*,original=None):
        if not body.confirm:raise ValueError('Подтвердите точные условия сделки')
        with self.store.connection(True) as c:
            current=self.store.campaign(campaign,c)
            def transition(state):
                outside_combat(state)
                row=c.execute('SELECT * FROM trade_offers WHERE id=? AND campaign=?',(body.offer,campaign)).fetchone()
                if not row:raise ValueError('Предложение не найдено')
                payload=json.loads(row['payload'])
                if row['status'] not in ('ready','counter') or row['expires']<time.time():raise ValueError('Предложение закрыто, отклонено или истекло')
                if payload['base_version']!=current['version'] or payload['state_hash']!=digest(state):raise ValueError('Состояние изменилось; запросите новые условия')
                engine=InventoryEngine(state);terms=payload['public']['terms'];received=apply_terms(engine,terms)
                c.execute("UPDATE trade_offers SET status='applied' WHERE id=?",(body.offer,))
                return [dict(type='scripted',kind='trade',text=f'Сделка подтверждена: {payload["public"]["merchant"]}. Оплачено {terms["pay_cp"]} CP, получено {terms["receive_cp"]} CP.'+(' Получены предметы: '+', '.join(received)+'.' if received else ''),offer=body.offer,pay_cp=terms['pay_cp'],receive_cp=terms['receive_cp'])]
            return self.store.engine_event(c,current,body.request_id,original or 'Подтверждение сделки',transition,digest(body.model_dump(exclude={'request_id'})))
