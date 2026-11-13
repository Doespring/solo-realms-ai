"""Allowlisted player/NPC commands over the same canonical journal, geometry and rolls."""
import copy,re
from typing import Literal
from pydantic import Field
from .contracts import Strict,Hero,Master
from .combat import Combatant,CombatEngine,WEAPONS,footprint,incapacitated,hostile
from .combat_view import refresh_vision,project
from .geometry import Position,rectangular_map,BattleMap
from .maps import MapDescription,generate,validate,TEMPLATES
from .rulesets import digest
from .check_text import attack_text

class CombatCommand(Strict):
    request_id: str = Field(min_length=1,max_length=80)
    expected_version: int | None = Field(default=None,ge=0)
    kind: Literal['move','attack','dash','dodge','disengage','stand_up','end_turn','npc_turn','reaction','finish','speak','observe','bonus_dash','bonus_disengage','interact','search','drop','inspiration','inspiration_gift','stabilize','temp_hp']
    position: Position | None = None
    target: str = Field(default='',max_length=80)
    choice: Literal['attack','decline'] | None = None
    nonlethal: bool = False
    #: PHB 2024, page 12: характеристику атаки выбирает игрок в рамках правила
    #: (фехтовальное оружие). Движок отвергает выбор, которого правило не даёт.
    ability: Literal['strength','dexterity'] | None = None
    #: PHB 2024, page 13: потратить Героическое вдохновение на переброс к20.
    inspiration: bool = False
    #: Временные хиты: сколько дают и какие оставить — старые или новые.
    amount: int = Field(default=0,ge=0,le=1000)
    keep: Literal['existing','new'] = 'existing'
    text: str = Field(default='',max_length=800)
    confirm: bool = False

class StartEncounter(Strict):
    request_id: str = Field(min_length=1,max_length=80)
    expected_version: int | None = Field(default=None,ge=0)
    confirm: bool = False
    template: Literal['room','forest_road','ruins','bridge','balcony','cellar'] = 'room'
    description: MapDescription | None = None
    imported_map: BattleMap | None = None
    hero_position: Position | None = None
    enemy_position: Position | None = None
    proposal: str = Field(default='',max_length=80)
    hero_weapon: Literal['unarmed','club','longsword','dagger','shortbow'] = 'unarmed'

def hero_of(engine):return next(a for a in engine.actors.values() if a.team=='hero')

def npc_turn(engine,*,finish=True):
    actor=engine.current()
    if actor.team=='hero':raise ValueError('За героя решение принимает игрок')
    if incapacitated(actor) or (actor.surprised and not actor.first_turn_finished and engine.rules.preset.surprise=='skip_first_turn'):
        return engine.end_turn(actor.id)
    candidates=[a for a in engine.actors.values() if hostile(a,actor) and not a.dead and engine.sees(actor,a) and not ('charmed' in actor.stats.conditions and actor.condition_sources.get('charmed')==a.id)]
    target=min(candidates,key=lambda a:engine.geometry.distance(actor.position.key(),a.position.key())) if candidates else None
    if target:actor.last_seen_hero=target.position.model_copy()
    results=[];weapon=WEAPONS[actor.weapon]
    limit=weapon.range_ft if weapon.ranged else weapon.reach_ft
    if target and engine.geometry.distance(actor.position.key(),target.position.key())<=limit and actor.action:
        results.append(engine.attack(actor.id,target.id))
    elif actor.last_seen_hero:
        goals=[]
        budget=max(0,engine.speed(actor)+actor.dash_movement-actor.movement_spent)/(2 if 'prone' in actor.stats.conditions else 1)
        # R172: a Large creature blocks its whole space, not only its anchor cell.
        occupied={cell for a in engine.actors.values() if a.id!=actor.id and not a.dead and engine.sees(actor,a) for cell in footprint(a)}
        reachable=engine.geometry.reachable(actor.position.key(),occupied=occupied,budget=budget,diagonals=actor.diagonals)
        # Reachable cells come from one bounded search. If the foe is distant, approach its last observed position.
        for pos,cost in reachable.items():
            distance=engine.geometry.distance(pos,actor.last_seen_hero.key())
            in_range=distance<=limit and engine.geometry.line_of_sight(pos,actor.last_seen_hero.key())
            goals.append((0 if in_range else 1,cost if in_range else distance,cost,pos))
        if goals:
            _,_,_,goal=min(goals)
            try:results+=engine.move(actor.id,goal)
            except ValueError:pass # A condition can prevent the chosen approach; the NPC holds position.
            if engine.data.get('pending_reaction') or engine.data.get('pending_death'):return results
            if target and not incapacitated(actor) and actor.action and not target.dead and engine.sees(actor,target) and engine.geometry.distance(actor.position.key(),target.position.key())<=limit:results.append(engine.attack(actor.id,target.id))
    if finish and not engine.data.get('pending_death') and not engine.data.get('pending_reaction'):results+=engine.end_turn(actor.id)
    return results

def summarize(engine,operations):
    hero=hero_of(engine);sentences=[]
    for op in operations:
        actor=engine.actors.get(op.get('actor'));target=engine.actors.get(op.get('target'))
        seen=actor is None or actor.id==hero.id or engine.sees(hero,actor)
        if op['kind']=='attack':
            if not seen and (not target or target.id!=hero.id):continue
            name=actor.stats.name if seen else 'Невидимый нападающий'
            victim=target.stats.name if target and (target.id==hero.id or engine.sees(hero,target)) else 'цель'
            # R172: the roll line comes from one formatter, so Помеха/Преимущество
            # and their reason are visible to the player, not only inside the record.
            line=attack_text(op)
            damage=f" Урон {op['hp_damage']}." if op.get('hit') else ''
            if op.get('knockout'):damage+=' Нокаут: Хиты 1 и Бессознательность.'
            elif op.get('bloodied'):damage+=' Цель Окровавлена.'
            sentences.append(f"{name}: атака по {victim}. {line}.{damage}")
        elif op['kind'] in ('inspiration','inspiration_gift') and op.get('actor')==hero.id:
            sentences.append(op['text'])
        elif op['kind']=='death_save':sentences.append(f"Спасбросок от смерти: {op['die']}; успехов {op['successes']}, провалов {op['failures']}.")
        elif op['kind']=='temp_hp' and op.get('actor')==hero.id:sentences.append(op['text'])
        elif op['kind']=='stabilize':sentences.append(op['text'])
        elif op['kind']=='condition_end' and seen:sentences.append(op['text'])
        elif op['kind']=='stabilized_recovery':sentences.append(op['text'])
        elif op['kind']=='reaction_pending':sentences.append('Доступна провоцированная атака. Игрок выбирает реакцию или отказ.')
        elif op['kind']=='movement_stop' and seen:sentences.append(op['text'])
        elif op['kind'] in ('interact','search','fall') and seen:sentences.append(op['text'])
        elif op['kind'] in ('dash','dodge','disengage','stand_up') and seen:sentences.append({'dash':'Рывок добавил движение.','dodge':'Уклонение принято.','disengage':'Отход предотвращает провоцированные атаки.','stand_up':'Участник встал.'}[op['kind']])
    if any(op['kind']=='move_step' and (op.get('actor')==hero.id or engine.sees(hero,engine.actors[op['actor']])) for op in operations):sentences.append('Перемещение рассчитано по клеткам и высотам карты.')
    if engine.data.get('pending_death'):sentences.append('Механически смертельный исход сохранён. Без заранее объявленного риска необратимый переход остановлен; требуется решение о последствиях. Доступен откат или сохранение.')
    if not engine.data['active']:sentences.append('Бой завершён: '+('поражение' if engine.data.get('ended')=='defeat' else 'победа')+'.')
    return ' '.join(sentences) or 'Ход завершён по очередности инициативы.'

def safe_operations(engine,operations):
    hero=hero_of(engine)
    public=[]
    for op in operations:
        actor=engine.actors.get(op.get('actor'))
        # Exact step positions are private unless the hero observed that actor at that step.
        if op['kind']=='move_step':
            if actor and actor.id==hero.id:public.append(op)
            continue
        if actor and actor.id!=hero.id and not engine.sees(hero,actor):
            if op['kind']=='attack' and op.get('target')==hero.id:
                # R172: the new attack fields carry the same secrets as 'weapon'/'roll'
                # (the hidden attacker's weapon name, its modifiers and AC data).
                # R173: 'ability', 'ability_reason' and the inspiration record (both
                # d20 values, the remaining charges) are the same class of secret.
                public.append({k:v for k,v in op.items() if k not in ('actor','weapon','weapon_name','distance_ft','cover_bonus','target_ac','roll','damage_dice','advantage_reasons','disadvantage_reasons','aimed_at','ability','ability_reason','inspiration')})
            continue
        public.append(op)
    return public

class CombatService:
    def __init__(self,store,*,rng=None):self.store=store;self.rng=rng
    def start_transition(self,current,body,state):
        if body.expected_version is not None and current['version']!=body.expected_version:raise ValueError('Сцена изменилась; обновите её перед началом боя')
        if state.get('economy'):
            # R173: КЗ в бою — то же число, что в листе. Лист пишет
            # единственный расчёт (solo/armor_class.py), поэтому перед боем он
            # пересчитывается, а не копируется устаревшим.
            from .inventory import InventoryEngine
            inventory=InventoryEngine(state);inventory.recalculate_ac();inventory.flush()
        hero=Combatant(id='hero',stats=Hero.model_validate(state['hero']),position=Position(x=1,y=4),team='hero',weapon=body.hero_weapon,ammo=20 if body.hero_weapon=='shortbow' else 0)
        if not state.get('economy') and body.hero_weapon!='unarmed':
            from .inventory import InventoryEngine,new_item
            inventory=InventoryEngine(state)
            item=new_item(body.hero_weapon,1,'hero','Первоначальное снаряжение явно подтверждено при начале боя')
            inventory.data.items.append(item);inventory.equip(item.id,'weapon')
            if body.hero_weapon=='shortbow':inventory.data.items.append(new_item('arrows',20,'hero','20 стрел явно подтверждены при начале боя'))
            inventory.assert_capacity();inventory.flush()
        if state.get('economy'):
            from .inventory import InventoryEngine
            hero.weapon,hero.ammo=InventoryEngine(state).loadout()
        foe=Combatant(id='foe-1',stats=Hero(name='Противник',hp=10,max_hp=10,ac=12,weapon_proficiencies=['simple']),position=Position(x=8,y=4),team='enemy',weapon='club')
        field=rectangular_map(12,10);field.name=state['location']+' · подтверждённое столкновение'
        pending=state.get('world',{}).get('pending_encounter')
        if body.proposal:
            if not pending or pending['id']!=body.proposal or pending['location']!=state['location']:raise ValueError('Предложение столкновения изменилось или относится к другой сцене')
            if body.imported_map:raise ValueError('Предложение использует подготовленную геометрию')
            field,hero.position,foe.position=generate(MapDescription.model_validate(pending['description']))
        elif body.imported_map:
            if len(body.imported_map.cells)>4800:raise ValueError('Загружаемая карта превышает 4800 поверхностей')
            if not body.hero_position or not body.enemy_position:raise ValueError('Укажите начальные позиции импортированной карты')
            field=body.imported_map.model_copy(deep=True);validate(field,body.hero_position,body.enemy_position);hero.position=body.hero_position;foe.position=body.enemy_position
        elif body.description or body.template!='room':
            description=body.description or MapDescription(environment=body.template)
            if description.environment!=body.template:raise ValueError('Описание не соответствует шаблону')
            field,hero.position,foe.position=generate(description)
        announced=Master.model_validate(current['master']).declared_lethal_risk or bool(body.proposal and pending['lethal_risk_announced'])
        engine=CombatEngine.start(state,field,[hero,foe],lethal_risk_declared=announced,rng=self.rng)
        if body.proposal:state['world']['pending_encounter']=None
        return [dict(type='scripted',kind='combat_start',text='Столкновение подтверждено игроком. Инициатива определена. '+('Серьёзный смертельный риск объявлен заранее.' if engine.data['lethal_risk_declared'] else 'Смертельный риск не объявлен: необратимый исход будет остановлен.'),ruleset=engine.rules.preset.model_dump())]

    def start(self,campaign,body,*,original=None):
        from .first_playable import require_full_scene
        require_full_scene(self.store.campaign(campaign)['state'])
        if not body.confirm:raise ValueError('Сначала подтвердите добавление столкновения в текущую локацию')
        with self.store.connection(True) as c:
            current=self.store.campaign(campaign,c)
            def transition(state):
                return self.start_transition(current,body,state)
            return self.store.engine_event(c,current,body.request_id,original or 'Начало подтверждённого столкновения',transition,digest(body.model_dump(exclude={'request_id'})))
    def execute_transition(self,current,body,state):
        if body.expected_version is not None and body.expected_version!=current['version']:raise ValueError('Сцена изменилась; обновите команду')
        if not state.get('combat',{}).get('active'):raise ValueError('Нет активного боя')
        engine=CombatEngine(state,rng=self.rng);hero=hero_of(engine)
        if engine.data.get('pending_death'):raise ValueError('Необратимый переход остановлен. Сохраните результат или откатите ход; окончательное правило последствий не утверждено.')
        if engine.data.get('pending_reaction') and body.kind!='reaction':raise ValueError('Сначала выберите реакцию')
        operations=[]
        if body.kind in ('speak','observe'):
            if body.kind=='speak' and (not body.text.strip() or engine.current().id!=hero.id or incapacitated(hero)):raise ValueError('Реплика недоступна в этом состоянии или ходе')
            if body.target and not any(body.target.lower() in (a.id.lower(),a.stats.name.lower()) and engine.sees(hero,a) for a in engine.actors.values()):raise ValueError('Адресат сейчас недоступен')
            return [dict(type='scripted',kind='combat_dialogue',text=('Герой произносит: '+body.text if body.kind=='speak' else 'Герой осмотрел доступную ему часть карты. Позиции и участники в поле зрения показаны на карте.'))]
        if body.kind=='move':
            if body.position is None:raise ValueError('Укажите клетку назначения')
            operations=engine.move(hero.id,body.position.key())
        elif body.kind=='attack':
            target=engine.actors.get(body.target)
            if target is None or target.id==hero.id:raise ValueError('Цель недоступна сейчас')
            # R172: an unseen target may still be attacked at its last known cell
            # (Disadvantage) — the engine decides about a wrong cell; a target
            # nobody ever saw keeps the refusal.
            if not engine.sees(hero,target) and engine.known_position(hero,target) is None:raise ValueError('Вы не знаете, где находится цель')
            operations=[engine.attack(hero.id,target.id,nonlethal=body.nonlethal,ability=body.ability,inspiration=body.inspiration)]
        elif body.kind=='inspiration':
            # R173: вдохновение — ресурс листа; излишек передаётся другому
            # герою группы, а не копится вторым.
            operations=[engine.gain_inspiration(hero.id,gift_to=body.target or None)]
        elif body.kind=='inspiration_gift':
            if not body.target:raise ValueError('Укажите, кому передать вдохновение')
            operations=[engine.give_inspiration(hero.id,body.target)]
        elif body.kind in ('dash','dodge','disengage'):operations=[engine.defend(hero.id,body.kind)]
        elif body.kind in ('bonus_dash','bonus_disengage'):operations=[engine.bonus(hero.id,body.kind.removeprefix('bonus_'))]
        elif body.kind=='stand_up':operations=[engine.stand_up(hero.id)]
        elif body.kind=='interact':operations=[engine.interact(hero.id,body.target)]
        elif body.kind=='search':
            if body.position is None:raise ValueError('Укажите клетку поиска')
            operations=[engine.search(hero.id,body.position.key())]
        elif body.kind=='drop':
            if body.position is None:raise ValueError('Укажите поверхность приземления')
            if not body.confirm:raise ValueError(engine.fall_preview(hero.id,body.position.key())['warning']+' Текст: «Подтверждаю падение на клетку X, Y, Z».')
            operations=[engine.drop(hero.id,body.position.key())]
        elif body.kind=='stabilize':
            # PHB 2024, печ. стр. 29: действие Помощь и Мудрость (Медицина) Сл 10.
            # Помощь оказывает тот, чей сейчас ход: это его действие.
            target=engine.actors.get(body.target)
            if target is None:raise ValueError('Укажите, кому оказывать помощь')
            operations=[engine.stabilize(engine.current().id,target.id)]
        elif body.kind=='temp_hp':
            if body.amount<=0:raise ValueError('Укажите количество Временных хитов')
            operations=[engine.grant_temp_hp(hero.id,body.amount,keep=body.keep)]
        elif body.kind=='end_turn':operations=engine.end_turn(hero.id)
        elif body.kind=='npc_turn':operations=npc_turn(engine)
        elif body.kind=='reaction':
            operations=engine.resolve_reaction(body.choice)
            if not engine.data.get('pending_reaction') and not engine.data.get('pending_death') and engine.current().team!='hero':operations+=npc_turn(engine)
        elif body.kind=='finish':
            if engine.data.get('pending_death') or hero.stats.hp==0 or hero.dead or any(a.team=='enemy' and a.stats.hp>0 and not a.dead for a in engine.actors.values()):raise ValueError('Нельзя завершить неразрешённый бой')
            engine.data.update(active=False,ended='victory')
        refresh_vision(engine);engine.flush()
        return [dict(type='scripted',kind='combat',text=summarize(engine,operations),operations=safe_operations(engine,operations),ruleset={'id':engine.rules.preset.id,'version':engine.rules.preset.version})]

    def execute(self,campaign,body,*,original=None):
        with self.store.connection(True) as c:
            current=self.store.campaign(campaign,c)
            def transition(state):
                return self.execute_transition(current,body,state)
            labels={'move':'Перемещение на карте','attack':'Атака выбранной цели','dash':'Рывок','dodge':'Уклонение','disengage':'Отход','stand_up':'Подъём','end_turn':'Завершение хода','npc_turn':'Ход NPC','reaction':'Выбор реакции','finish':'Завершение боя','bonus_dash':'Бонусный рывок','bonus_disengage':'Бонусный отход','speak':body.text,'observe':'Осмотр сцены','interact':'Взаимодействие с объектом','search':'Поиск на выбранной клетке','drop':'Спуск с падением','inspiration':'Героическое вдохновение','inspiration_gift':'Передача вдохновения','stabilize':'Стабилизация и первая помощь','temp_hp':'Временные хиты'}
            return self.store.engine_event(c,current,body.request_id,original or labels[body.kind],transition,digest(body.model_dump(exclude={'request_id'})))
