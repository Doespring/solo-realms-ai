"""Inventory and trade endpoints; precise text shares the same service."""
import re,json,time
from .inventory import CATALOG,InventoryEngine
from .inventory_service import InventoryService,InventoryCommand,MerchantSetup,TradeDraft,TradeApproval

ALIASES={'club':['дубинку','дубинки'],'longsword':['меч','длинный меч'],'shortbow':['лук','короткий лук'],'arrows':['стрела','стрелу','стрел','стрелы'],'leather':['кожаную броню','кожаная броня'],'chain_mail':['кольчуга','кольчугу'],'shield':['щит'],'healing_potion':['зелье','лечебное зелье','зелье лечения'],'rations':['паек','пайки','пайка','рацион'],'rope':['веревка','веревку','веревки','веревку 50 футов']}
def normal(value):return value.lower().replace('ё','е').strip(' «»"')
def names(name):
    name=normal(name)
    result={normal(name)}
    if name.endswith('а'):result.update(normal(name[:-1]+tail) for tail in ('ы','е','у','ой','ою'))
    elif name.endswith('я'):result.update(normal(name[:-1]+tail) for tail in ('и','е','ю','ей'))
    return result

def text_command(text,state,request_id):
    normalized=text.lower().strip().rstrip('.! ')
    grant=re.fullmatch(r'подтверждаю получение:\s*(.+?);\s*количество:\s*(\d{1,4});\s*источник:\s*(.+)',text,re.I)
    if grant:
        catalogs=[id for id,d in CATALOG.items() if grant[1].lower().strip() in (id,d['name'].lower())]
        if len(catalogs)!=1:raise ValueError('Укажите зарегистрированный предмет из каталога')
        return InventoryCommand(request_id=request_id,kind='grant',catalog=catalogs[0],quantity=int(grant[2]),source=grant[3].strip(),confirm=True)
    wallet=re.fullmatch(r'подтверждаю кошел[её]к:\s*(\d{1,10})\s*cp;\s*источник:\s*(.+)',text,re.I)
    if wallet:return InventoryCommand(request_id=request_id,kind='wallet',wallet_cp=int(wallet[1]),source=wallet[2].strip(),confirm=True)
    merchant=re.fullmatch(r'подтверждаю торговца:\s*(\{.*\})',text,re.I|re.S)
    if merchant:return MerchantSetup.model_validate({**json.loads(merchant[1]),'request_id':request_id,'confirm':True})
    approval=re.fullmatch(r'подтверждаю\s+сделку\s+([a-z0-9-]{36})',normalized)
    if approval:return TradeApproval(request_id=request_id,offer=approval[1],confirm=True)
    if re.search(r'[;]|\b(?:затем|потом|после этого|независимо|в любом случае)\b',text,re.I):return None
    public=InventoryEngine(state).public()
    barter=re.fullmatch(r'предлагаю обмен:\s*(\{.*\})',text,re.I|re.S)
    if barter:
        value=json.loads(barter[1]);matches=[m for m in public['merchants'] if str(value.get('merchant','')).lower() in (m['id'].lower(),m['name'].lower())]
        if len(matches)!=1:raise ValueError('Укажите присутствующего торговца')
        return TradeDraft.model_validate({**value,'request_id':request_id,'kind':'barter','merchant':matches[0]['id']})
    def find(name,items):
        name=normal(name)
        found=[i for i in items if name in {normal(i['name']),normal(i['id']),*[normal(n) for n in ALIASES.get(i['catalog'],[])]}]
        if len(found)>1 and found[0]['catalog'] and len({(i['catalog'],i['condition']) for i in found})==1:return found[0]
        if len(found)!=1:raise ValueError('Укажите один доступный предмет точно по названию или ID')
        return found[0]
    potion=re.fullmatch(r'(?:выпиваю|пью)\s+(?:лечебное\s+)?зелье',normalized)
    if potion:
        candidates=[i for i in public['items'] if i['catalog']=='healing_potion']
        if not candidates:raise ValueError('В инвентаре нет лечебного зелья')
        return InventoryCommand(request_id=request_id,kind='use',item=candidates[0]['id'])
    equip=re.fullmatch(r'(экипирую|использую|выбрасываю|надеваю|беру в руки)\s+(.+)',normalized)
    if equip:
        item=find(equip[2],public['items']);kind={'экипирую':'equip','надеваю':'equip','беру в руки':'equip','использую':'use','выбрасываю':'discard'}[equip[1]]
        slot='shield' if item['catalog']=='shield' else 'armor' if item['type']=='armor' else 'weapon'
        return InventoryCommand(request_id=request_id,kind=kind,item=item['id'],slot=slot)
    unequip=re.fullmatch(r'снимаю\s+(оружие|броню|щит)',normalized)
    if unequip:return InventoryCommand(request_id=request_id,kind='unequip',slot={'оружие':'weapon','броню':'armor','щит':'shield'}[unequip[1]])
    give=re.fullmatch(r'передаю\s+(.+?)\s+персонажу\s+(.+)',normalized)
    if give:
        npc=[n for p in state['locations'] if p['name']==state['location'] for n in p['npc'] if n.get('present',True) and normal(give[2]) in names(n['name'])]
        if len(npc)!=1:raise ValueError('Укажите присутствующего получателя')
        number=re.fullmatch(r'(\d{1,5})\s+(.+)',give[1]);quantity=int(number[1]) if number else 1
        return InventoryCommand(request_id=request_id,kind='give',item=find(number[2] if number else give[1],public['items'])['id'],recipient=npc[0]['name'],quantity=quantity)
    trade=re.fullmatch(r'(покупаю|продаю)\s+(.+?)\s+(?:у|торговцу)\s+(.+)',normalized)
    if trade:
        merchants=[m for m in public['merchants'] if normal(trade[3]) in names(m['name'])|{normal(m['id'])}]
        if len(merchants)!=1:raise ValueError('Укажите присутствующего торговца')
        merchant=merchants[0];buy=trade[1]=='покупаю';number=re.fullmatch(r'(\d{1,5})\s+(.+)',trade[2]);quantity=int(number[1]) if number else 1
        item=find(number[2] if number else trade[2],merchant['stock'] if buy else public['items'])
        return TradeDraft(request_id=request_id,merchant=merchant['id'],kind='buy' if buy else 'sell',**{'take' if buy else 'give':[dict(item=item['id'],quantity=quantity)]})
    return None

def register_inventory_api(app,store,game):
    service=InventoryService(store)
    @app.get('/api/items/catalog')
    def catalog():return [dict(id=id,**value) for id,value in CATALOG.items()]
    @app.get('/api/campaigns/{campaign}/inventory')
    def inventory(campaign:str):return InventoryEngine(store.campaign(campaign)['state']).public()
    @app.post('/api/campaigns/{campaign}/inventory/command')
    async def command(campaign:str,body:InventoryCommand):
        async with game.operation:return game.public_event(service.execute(campaign,body))
    @app.post('/api/campaigns/{campaign}/merchants')
    async def merchant(campaign:str,body:MerchantSetup):
        async with game.operation:return game.public_event(service.merchant(campaign,body))
    @app.post('/api/campaigns/{campaign}/trade/quote')
    async def quote(campaign:str,body:TradeDraft):
        async with game.operation:return service.quote(campaign,body)
    @app.get('/api/campaigns/{campaign}/trade/offers')
    def offers(campaign:str):
        current=store.campaign(campaign);result=[]
        with store.connection() as c:
            for row in c.execute('SELECT * FROM trade_offers WHERE campaign=? ORDER BY created DESC LIMIT 30',(campaign,)):
                payload=json.loads(row['payload']);status=row['status']
                if status in ('ready','counter'):
                    if row['expires']<time.time():status='expired'
                    elif payload['base_version']!=current['version']:status='stale'
                result.append({**payload['public'],'status':status})
        return result
    @app.post('/api/campaigns/{campaign}/trade/apply')
    async def apply(campaign:str,body:TradeApproval):
        async with game.operation:return game.public_event(service.apply(campaign,body))
