"""Registered items, ownership, equipment and integer game currency. No AI effects."""
import math,secrets,uuid,copy
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Strict,Hero
from .rulesets import active,RulesetEngine
from .armor_class import armor_class

class Item(Strict):
    id: str = Field(min_length=1,max_length=80)
    catalog: str = Field(default='',max_length=80)
    type: Literal['weapon','armor','consumable','ammunition','gear'] = 'gear'
    name: str = Field(min_length=1,max_length=120)
    quantity: int = Field(default=1,ge=0,le=10000)
    weight_centilb: int = Field(default=0,ge=0,le=100000)
    value_cp: int = Field(default=0,ge=0,le=100000000)
    properties: list[str] = Field(default_factory=list,max_length=20)
    description: str = Field(default='',max_length=2000)
    condition: Literal['intact','worn','damaged','broken']='intact'
    owner: str = Field(default='hero',min_length=1,max_length=160)
    source: str = Field(min_length=1,max_length=1000)
    parameters: dict[str,str|int|bool] = Field(default_factory=dict,max_length=20)
    @model_validator(mode='after')
    def valid(self):
        if any(len(x)>300 for x in self.properties) or any(len(k)>80 or isinstance(v,str) and len(v)>300 for k,v in self.parameters.items()):raise ValueError('Параметры предмета слишком длинные')
        return self

CATALOG={
 'club':dict(name='Дубинка',type='weapon',weight_centilb=200,value_cp=10,properties=['простое','одноручное'],parameters={'weapon':'club'}),
 'longsword':dict(name='Длинный меч',type='weapon',weight_centilb=300,value_cp=1500,properties=['воинское','одноручное'],parameters={'weapon':'longsword'}),
 'dagger':dict(name='Кинжал',type='weapon',weight_centilb=100,value_cp=200,properties=['простое','лёгкое','фехтовальное'],parameters={'weapon':'dagger'}),
 'shortbow':dict(name='Короткий лук',type='weapon',weight_centilb=200,value_cp=2500,properties=['простое','двуручное'],parameters={'weapon':'shortbow'}),
 'arrows':dict(name='Стрелы',type='ammunition',weight_centilb=5,value_cp=5),
 'leather':dict(name='Кожаная броня',type='armor',weight_centilb=1000,value_cp=1000,properties=['лёгкая броня'],parameters={'armor':'light','base_ac':11,'dex_cap':100}),
 'chain_mail':dict(name='Кольчуга',type='armor',weight_centilb=5500,value_cp=7500,properties=['тяжёлая броня','Сила 13'],parameters={'armor':'heavy','base_ac':16,'dex_cap':0,'strength_required':13}),
 'shield':dict(name='Щит',type='armor',weight_centilb=600,value_cp=1000,properties=['щит'],parameters={'armor':'shields','ac_bonus':2}),
 'healing_potion':dict(name='Лечебное зелье',type='consumable',weight_centilb=50,value_cp=5000,description='Восстанавливает 2d4+2 HP. Только обычное зелье лечения.'),
 'rations':dict(name='Паёк',type='consumable',weight_centilb=200,value_cp=50,description='Один суточный рацион. Не заменяет воду и сам по себе не отменяет истощение.'),
 'rope':dict(name='Верёвка 50 футов',type='gear',weight_centilb=500,value_cp=100,description='Запись снаряжения; лазание не создаётся из названия автоматически.') }

class Merchant(Strict):
    id: str = Field(min_length=1,max_length=80)
    name: str = Field(min_length=1,max_length=80)
    location: str = Field(min_length=1,max_length=120)
    money_cp: int = Field(default=10000,ge=0,le=1000000000)
    buy_percent: int = Field(default=50,ge=0,le=100)
    sell_percent: int = Field(default=100,ge=1,le=300)
    relationship: int = Field(default=0,ge=-100,le=100)
    refuse_below: int = Field(default=-50,ge=-100,le=100)
    accepts: list[Literal['weapon','armor','consumable','ammunition','gear']] = Field(default_factory=lambda:['weapon','armor','consumable','ammunition','gear'],max_length=5)
    available: bool = True

class Economy(Strict):
    items: list[Item] = Field(default_factory=list,max_length=1000)
    wallet_cp: int = Field(default=0,ge=0,le=1000000000)
    equipment: dict[Literal['weapon','armor','shield'],str] = Field(default_factory=dict,max_length=3)
    merchants: list[Merchant] = Field(default_factory=list,max_length=100)
    base_ac: int | None = Field(default=None,ge=1,le=40)
    @model_validator(mode='after')
    def valid(self):
        if len({i.id for i in self.items})!=len(self.items) or len({m.id for m in self.merchants})!=len(self.merchants):raise ValueError('Повтор идентификатора предмета или торговца')
        if any(not any(i.id==id and i.owner=='hero' and i.quantity>0 for i in self.items) for id in self.equipment.values()):raise ValueError('Экипировка ссылается на отсутствующий предмет')
        return self

def new_item(catalog,quantity,owner,source):
    if catalog not in CATALOG:raise ValueError('Предмет отсутствует в зарегистрированном каталоге')
    return Item(id=str(uuid.uuid4()),catalog=catalog,quantity=quantity,owner=owner,source=source,**CATALOG[catalog])

class InventoryEngine:
    def __init__(self,state,*,rng=None):
        self.state=state;self.data=Economy.model_validate(state.get('economy',{}));self.rules=RulesetEngine(active(state));self.rng=rng or secrets.SystemRandom()
    def flush(self):self.state['economy']=Economy.model_validate(self.data.model_dump()).model_dump()
    def item(self,id,owner='hero'):
        item=next((i for i in self.data.items if i.id==id and i.owner==owner and i.quantity),None)
        if not item:raise ValueError('Предмет или количество недоступны')
        return item
    def weight(self):
        # Wallet is represented by ordinary denominations, 50 coins per pound.
        value=self.data.wallet_cp;coins=0
        for size in (1000,100,10,1):n,value=divmod(value,size);coins+=n
        return sum(i.quantity*i.weight_centilb for i in self.data.items if i.owner=='hero')+coins*2
    def capacity(self):
        hero=Hero.model_validate(self.state['hero'])
        return hero.abilities['strength']*active(self.state).carry_multiplier*100*(2 if hero.size=='Large' else 1)
    def assert_capacity(self):
        if self.weight()>self.capacity():raise ValueError('Вес превышает грузоподъёмность; освободите место')
    def available_merchant(self,id):
        merchant=next((m for m in self.data.merchants if m.id==id and m.location==self.state['location'] and m.available),None)
        if not merchant:raise ValueError('Торговец сейчас недоступен')
        npc=next((n for p in self.state['locations'] if p['name']==self.state['location'] for n in p['npc'] if n['name']==merchant.name and n.get('present',True)),None)
        if not npc:raise ValueError('Торговец отсутствует в сцене')
        return merchant
    def transfer(self,item_id,quantity,recipient,*,owner='hero',source='Передача'):
        item=self.item(item_id,owner)
        if quantity<1 or quantity>item.quantity:raise ValueError('Недостаточно предметов')
        if item_id in self.data.equipment.values():raise ValueError('Сначала снимите предмет с экипировки')
        item.quantity-=quantity
        received=Item.model_validate({**item.model_dump(),'id':str(uuid.uuid4()),'quantity':quantity,'owner':recipient,'source':(item.source+' → '+source)[:1000]})
        self.data.items.append(received)
        return received
    def armor_parameters(self):
        """Параметры надетой брони и щита — вход единственного расчёта КЗ."""
        armor=shield=None
        if self.data.equipment.get('armor'):
            armor=dict(CATALOG[self.item(self.data.equipment['armor']).catalog]['parameters'])
        if self.data.equipment.get('shield'):
            shield=dict(CATALOG[self.item(self.data.equipment['shield']).catalog]['parameters'])
        return armor,shield
    def recalculate_ac(self):
        """Пишет в лист то же число, что покажет бой (PHB 2024, «Класс Защиты»).

        Формулы больше не живут здесь: единственный расчёт —
        ``solo.armor_class.armor_class``. ``base_ac`` остаётся подтверждённой
        листом базой и участвует только тогда, когда брони нет.
        """
        hero=Hero.model_validate(self.state['hero'])
        if self.data.base_ac is None:self.data.base_ac=hero.ac
        armor,shield=self.armor_parameters()
        result=armor_class(hero,armor=armor,shield=shield,confirmed_base=None if armor else self.data.base_ac)
        self.state['hero']['ac']=result['ac']
        return result
    def equip(self,id,slot):
        if self.state.get('combat',{}).get('active'):raise ValueError('Смена снаряжения в бою пока не зарегистрирована; завершите бой')
        item=self.item(id)
        if item.condition in ('damaged','broken'):raise ValueError('Повреждённое снаряжение требует ремонта')
        definition=CATALOG.get(item.catalog)
        if not definition:raise ValueError('Для этого предмета эффект экипировки не зарегистрирован')
        if slot=='weapon':
            if item.type!='weapon':raise ValueError('Нужен зарегистрированный вид оружия')
            if item.catalog=='shortbow' and self.data.equipment.get('shield'):raise ValueError('Двуручный лук несовместим с щитом')
        elif slot in ('armor','shield'):
            armor=definition.get('parameters',{}).get('armor')
            if (slot=='shield')!=(armor=='shields') or not armor:raise ValueError('Предмет не подходит для этого слота')
            if armor not in self.state['hero'].get('armor_proficiencies',[]):raise ValueError('Добавьте владение этим типом брони в лист; эффекты ношения без владения ещё не реализованы')
            if slot=='shield' and self.data.equipment.get('weapon') and self.item(self.data.equipment['weapon']).catalog=='shortbow':raise ValueError('Щит несовместим с двуручным луком')
        else:raise ValueError('Неизвестный слот')
        self.data.equipment[slot]=id;self.recalculate_ac();return item.name+' экипировано.'
    def unequip(self,slot):
        if self.state.get('combat',{}).get('active'):raise ValueError('Смена снаряжения в бою пока не зарегистрирована')
        if slot not in self.data.equipment:raise ValueError('Слот уже свободен')
        del self.data.equipment[slot];self.recalculate_ac()
        return 'Снаряжение снято.'
    def use(self,id):
        item=self.item(id)
        if item.condition=='broken':raise ValueError('Повреждённый предмет нельзя использовать')
        battle=None;actor=None
        if self.state.get('combat',{}).get('active'):
            from .combat import CombatEngine,incapacitated
            battle=CombatEngine(self.state,rng=self.rng);actor=battle.actors['hero']
            if battle.current().id!='hero' or incapacitated(actor) or battle.data.get('pending_reaction') or battle.data.get('pending_death'):raise ValueError('Предмет сейчас недоступен')
        hero=actor.stats if actor else Hero.model_validate(self.state['hero'])
        if set(hero.conditions)&{'incapacitated','unconscious','paralyzed','stunned','petrified'}:raise ValueError('Герой не может использовать предмет в этом состоянии')
        if item.catalog=='healing_potion':
            if self.state.get('hero_death') or hero.hp==0:raise ValueError('Герой не может самостоятельно выпить зелье в этом состоянии')
            if actor:
                if self.rules.preset.potion_action=='bonus':
                    if not actor.bonus_action:raise ValueError('Бонусное действие уже использовано')
                    if actor.surprised and not actor.first_turn_finished and self.rules.preset.surprise=='skip_first_turn':raise ValueError('Внезапность запрещает действие')
                    actor.bonus_action=False
                else:battle.assert_action(actor);actor.action=False
            dice=[self.rng.randint(1,4),self.rng.randint(1,4)];before=hero.hp;hero.hp=min(self.rules.exhaustion_effects(hero)['effective_max_hp'],hero.hp+sum(dice)+2)
            result=dict(text=f'Лечебное зелье: восстановлено {hero.hp-before} HP.',dice=dice,hp_before=before,hp_after=hero.hp)
        elif item.catalog=='rations':
            if actor or hero.hp==0:raise ValueError('Суточный рацион расходуется вне боя')
            result=dict(text='Один суточный рацион израсходован. Вода и отдых учитываются отдельно.')
        else:raise ValueError('Для этого предмета использование не зарегистрировано')
        item.quantity-=1
        if battle:battle.flush()
        else:self.state['hero']=hero.model_dump()
        return result
    def spend_arrow(self):
        item=next((i for i in self.data.items if i.owner=='hero' and i.catalog=='arrows' and i.quantity>0),None)
        if not item:raise ValueError('Нет стрел')
        item.quantity-=1;self.flush()
    def loadout(self):
        id=self.data.equipment.get('weapon');weapon=self.item(id).catalog if id else 'unarmed'
        return weapon,sum(i.quantity for i in self.data.items if i.owner=='hero' and i.catalog=='arrows')
    def speed_penalty(self):
        id=self.data.equipment.get('armor')
        if not id:return 0
        definition=CATALOG.get(self.item(id).catalog,{}).get('parameters',{})
        return 10 if self.state['hero']['abilities']['strength']<definition.get('strength_required',0) else 0
    def price(self,merchant,item,*,buying_from_hero=False):
        condition={'intact':100,'worn':75,'damaged':50,'broken':0}[item.condition]
        relationship=merchant.relationship//10
        percent=merchant.buy_percent*(100+relationship) if buying_from_hero else merchant.sell_percent*(100-relationship)
        numerator=item.value_cp*condition*percent
        return numerator//1000000 if buying_from_hero else (numerator+999999)//1000000
    def public(self):
        merchants=[]
        for m in self.data.merchants:
            try:self.available_merchant(m.id)
            except ValueError:continue
            stock=[{**i.model_dump(),'price_cp':self.price(m,i)} for i in self.data.items if i.owner=='merchant:'+m.id and i.quantity]
            merchants.append(dict(id=m.id,name=m.name,stock=stock,relationship=m.relationship))
        return dict(items=[i.model_dump() for i in self.data.items if i.owner=='hero' and i.quantity],wallet_cp=self.data.wallet_cp,equipment=self.data.equipment,weight_centilb=self.weight(),capacity_centilb=self.capacity(),merchants=merchants)

def public_economy(state):return InventoryEngine(state).public() if state.get('economy') else None
