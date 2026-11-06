"""Registered tactical resolution. The narrator never rolls or mutates combat."""
import secrets
from typing import Literal
from pydantic import Field
from .contracts import Strict,Hero
from .rulesets import RulesetEngine,active
from .geometry import Position,BattleMap,Geometry,Cell

DamageType=Literal['bludgeoning','piercing','slashing','acid','cold','fire','force','lightning','necrotic','poison','psychic','radiant','thunder']

class Weapon(Strict):
    id: str
    name: str
    ability: Literal['strength','dexterity']
    dice_count: int = Field(default=1,ge=0,le=10)
    die: Literal[4,6,8,10,12] = 6
    flat: int = Field(default=0,ge=0,le=100)
    damage_type: DamageType
    reach_ft: int = Field(default=5,ge=5,le=30)
    ranged: bool = False
    range_ft: int = Field(default=0,ge=0,le=600)
    long_range_ft: int = Field(default=0,ge=0,le=1200)
    proficiency: Literal['simple','martial','all'] = 'simple'

WEAPONS={
 'unarmed':Weapon(id='unarmed',name='Безоружный удар',ability='strength',dice_count=0,flat=1,damage_type='bludgeoning',proficiency='all'),
 'club':Weapon(id='club',name='Дубинка',ability='strength',die=4,damage_type='bludgeoning'),
 'longsword':Weapon(id='longsword',name='Длинный меч',ability='strength',die=8,damage_type='slashing',proficiency='martial'),
 'shortbow':Weapon(id='shortbow',name='Короткий лук',ability='dexterity',die=6,damage_type='piercing',ranged=True,range_ft=80,long_range_ft=320)}

class Combatant(Strict):
    id: str = Field(min_length=1,max_length=80)
    stats: Hero
    position: Position
    team: Literal['hero','enemy','ally']
    weapon: Literal['unarmed','club','longsword','shortbow'] = 'unarmed'
    ammo: int = Field(default=0,ge=0,le=10000)
    temp_hp: int = Field(default=0,ge=0,le=1000)
    resistances: list[DamageType] = Field(default_factory=list,max_length=13)
    vulnerabilities: list[DamageType] = Field(default_factory=list,max_length=13)
    immunities: list[DamageType] = Field(default_factory=list,max_length=13)
    condition_sources: dict[str,str] = Field(default_factory=dict,max_length=14)
    darkvision_ft: int = Field(default=0,ge=0,le=300)
    vision_ft: int = Field(default=600,ge=5,le=1000)
    action: bool = True
    bonus_action: bool = True
    reaction: bool = True
    interaction: bool = True
    movement_spent: float = Field(default=0,ge=0,le=1000)
    diagonals: int = Field(default=0,ge=0,le=1000)
    dodging: bool = False
    disengaged: bool = False
    dash_movement: int = Field(default=0,ge=0,le=600)
    surprised: bool = False
    first_turn_finished: bool = False
    initiative: int = Field(default=0,ge=-100,le=100)
    initiative_dice: list[int] = Field(default_factory=list,max_length=2)
    dead: bool = False
    stable: bool = False
    death_successes: int = Field(default=0,ge=0,le=3)
    death_failures: int = Field(default=0,ge=0,le=3)
    last_seen_hero: Position | None = None

def incapacitated(actor):
    return actor.dead or actor.stats.hp==0 or bool(set(actor.stats.conditions)&{'incapacitated','unconscious','paralyzed','stunned','petrified'})

def hostile(a,b):
    return (a.team=='enemy') != (b.team=='enemy')

class CombatEngine:
    @staticmethod
    def start(state,map,actors,*,lethal_risk_declared=False,rng=None):
        if state.get('combat',{}).get('active'):raise ValueError('Бой уже идёт')
        if state.get('hero_death'):raise ValueError('Погибший герой не начинает новый бой')
        rng=rng or secrets.SystemRandom();rules=RulesetEngine(active(state));geometry=Geometry(map,rules.preset)
        if len(actors)<2 or len(actors)>30 or sum(a.team=='hero' for a in actors)!=1 or not any(a.team=='enemy' for a in actors):raise ValueError('Нужен один герой и хотя бы один противник')
        if len({a.id for a in actors})!=len(actors) or len({a.position.key() for a in actors})!=len(actors):raise ValueError('Повтор участника или начальной позиции')
        for actor in actors:
            if actor.stats.size=='Large':raise ValueError('Размер Large требует механики занимаемой площади; базовый бой пока поддерживает Small/Medium')
            if not geometry.can_stand(actor.position.key()) or not actor.stats.hp:raise ValueError('Участник не может начинать бой в этой клетке или состоянии')
            if rules.exhaustion_effects(actor.stats)['mechanical_death']:raise ValueError('Смертельное истощение требует отдельного решения')
            if actor.stats.conditions and any(c in actor.stats.conditions and not actor.condition_sources.get(c) for c in ('charmed','frightened')):raise ValueError('Для очарования или испуга нужно указать источник')
            if any(source not in {a.id for a in actors} for source in actor.condition_sources.values()):raise ValueError('Неизвестный источник состояния')
            initiative=rules.initiative(actor.stats,surprised=actor.surprised,rng=rng)
            actor.initiative=initiative['total'];actor.initiative_dice=initiative['dice'];actor.reaction=not initiative['skip_first_turn']
        # Deterministic tie order is an explicitly documented local ruling.
        order=[a.id for a in sorted(actors,key=lambda a:(-a.initiative,a.id))]
        state['combat']=dict(active=True,map=map.model_dump(),actors=[a.model_dump() for a in actors],order=order,cursor=0,round=1,
            lethal_risk_declared=lethal_risk_declared,explored=[],last_seen={},pending_reaction=None,pending_death=None)
        engine=CombatEngine(state,rng=rng);engine.begin_turn(engine.current())
        from .combat_view import refresh_vision
        refresh_vision(engine);engine.flush();return engine
    def __init__(self,state,*,rng=None):
        self.state=state;self.data=state.get('combat');self.rules=RulesetEngine(active(state));self.rng=rng or secrets.SystemRandom()
        self.map=BattleMap.model_validate(self.data['map']) if self.data else None
        self.geometry=Geometry(self.map,self.rules.preset) if self.map else None
        self.actors={a['id']:Combatant.model_validate(a) for a in self.data['actors']} if self.data else {}
    def flush(self):
        self.data['map']=self.map.model_dump()
        self.data['actors']=[a.model_dump() for a in self.actors.values()]
        heroes=[a for a in self.actors.values() if a.team=='hero']
        if heroes:self.state['hero']=heroes[0].stats.model_dump()
    def current(self):return self.actors[self.data['order'][self.data['cursor']]]
    def occupied(self,except_id=None):return {a.position.key() for a in self.actors.values() if a.id!=except_id and not a.dead}
    def sees(self,actor,target):
        if actor.dead or set(actor.stats.conditions)&{'blinded','unconscious','petrified'}:return False
        if isinstance(target,Combatant):
            if 'invisible' in target.stats.conditions and actor.id!=target.id:return False
            destination=target.position.key()
        else:destination=tuple(target)
        cell=self.geometry.cells.get(destination)
        if cell is None:return False
        distance=self.geometry.distance(actor.position.key(),destination)
        radius=actor.darkvision_ft if self.geometry.illumination(destination)=='dark' else actor.vision_ft
        return distance<=radius and self.geometry.line_of_sight(actor.position.key(),destination)
    def assert_action(self,actor,*,reaction=False):
        if not self.data or not self.data.get('active'):raise ValueError('Нет активного боя')
        if self.data.get('pending_death'):raise ValueError('Смертельный исход требует отдельного решения игрока')
        if self.data.get('pending_reaction') and not reaction:raise ValueError('Сначала выберите реакцию')
        if incapacitated(actor):raise ValueError('Участник не может действовать в этом состоянии')
        if reaction:
            if not actor.reaction:raise ValueError('Реакция уже использована или недоступна')
        else:
            if self.current().id!=actor.id:raise ValueError('Сейчас ход другого участника')
            if actor.surprised and not actor.first_turn_finished and self.rules.preset.surprise=='skip_first_turn':raise ValueError('Внезапность не позволяет действовать в первом ходу')
            if not actor.action:raise ValueError('Основное действие уже использовано')
    def begin_turn(self,actor):
        actor.action=actor.bonus_action=True;actor.reaction=not (actor.surprised and not actor.first_turn_finished and self.rules.preset.surprise=='skip_first_turn')
        actor.interaction=True
        actor.movement_spent=0;actor.diagonals=0;actor.dash_movement=0;actor.dodging=False;actor.disengaged=False
        return self.death_save(actor) if actor.team=='hero' else None
    def end_turn(self,actor_id):
        if self.data.get('pending_death') or self.data.get('pending_reaction'):raise ValueError('Сначала разрешите ожидающее решение')
        actor=self.actors[actor_id]
        if self.current().id!=actor_id:raise ValueError('Сейчас ход другого участника')
        actor.first_turn_finished=True
        if actor.surprised and self.rules.preset.surprise=='skip_first_turn':actor.reaction=True
        hero=next(a for a in self.actors.values() if a.team=='hero')
        if hero.dead:self.data.update(active=False,ended='defeat');return []
        if not any(a.team=='enemy' and a.stats.hp>0 and not a.dead for a in self.actors.values()) and (hero.stats.hp>0 or hero.stable):
            self.data.update(active=False,ended='victory');return []
        for _ in range(len(self.data['order'])):
            self.data['cursor']=(self.data['cursor']+1)%len(self.data['order'])
            if self.data['cursor']==0:
                self.data['round']+=1;self.state['elapsed_minutes']=self.state.get('elapsed_minutes',0)+0.1
            candidate=self.current()
            if not candidate.dead and (candidate.stats.hp>0 or candidate.team=='hero'):
                result=self.begin_turn(candidate);return [result] if result else []
        raise ValueError('Нет участника для следующего хода')
    def defend(self,actor_id,kind):
        actor=self.actors[actor_id];self.assert_action(actor)
        if kind=='dash':actor.dash_movement+=self.speed(actor)
        elif kind=='dodge':actor.dodging=True
        elif kind=='disengage':actor.disengaged=True
        else:raise ValueError('Неподдержанное действие')
        actor.action=False;return dict(kind=kind,actor=actor_id)
    def stand_up(self,actor_id):
        actor=self.actors[actor_id];self.assert_movement(actor)
        if 'prone' not in actor.stats.conditions:raise ValueError('Участник уже стоит')
        cost=self.speed(actor)/2
        if not cost or actor.movement_spent+cost>self.speed(actor)+actor.dash_movement:raise ValueError('Недостаточно движения, чтобы встать')
        actor.stats.conditions.remove('prone');actor.movement_spent+=cost;return dict(kind='stand_up',actor=actor_id,cost_ft=cost)
    def interact(self,actor_id,object_id):
        actor=self.actors[actor_id]
        if self.current().id!=actor.id or incapacitated(actor) or self.data.get('pending_reaction') or self.data.get('pending_death'):raise ValueError('Взаимодействие сейчас недоступно')
        if actor.surprised and not actor.first_turn_finished and self.rules.preset.surprise=='skip_first_turn':raise ValueError('Внезапность запрещает взаимодействие')
        if not actor.interaction:raise ValueError('Свободное взаимодействие в этом ходу уже использовано')
        obj=next((o for o in self.map.objects if o.id==object_id and not o.secret),None)
        if not obj or not self.sees(actor,obj.position.key()) or self.geometry.distance(actor.position.key(),obj.position.key())>5:raise ValueError('Объект недоступен')
        if obj.kind=='door':
            if obj.locked:raise ValueError('Дверь заперта; подходящий ключ или взлом ещё не зарегистрирован')
            if obj.open and obj.position.key() in self.occupied():raise ValueError('Нельзя закрыть дверь в занятой клетке')
            obj.open=not obj.open;cell=self.geometry.cells[obj.position.key()];cell.blocked=cell.opaque=not obj.open;cell.cover=0 if obj.open else 100
            text=obj.name+(' открыта.' if obj.open else ' закрыта.')
        elif obj.kind=='light':obj.enabled=not obj.enabled;text=obj.name+(' включён.' if obj.enabled else ' погашен.')
        else:raise ValueError('Для этого объекта механическое взаимодействие не зарегистрировано')
        actor.interaction=False;self.geometry=Geometry(self.map,self.rules.preset)
        return dict(kind='interact',actor=actor_id,object=obj.id,text=text)
    def search(self,actor_id,position):
        actor=self.actors[actor_id];self.assert_action(actor)
        if self.geometry.distance(actor.position.key(),position)>5 or not self.sees(actor,position):raise ValueError('Для поиска подойдите к видимой клетке')
        hidden=[o for o in self.map.objects if o.secret and o.position.key()==tuple(position)]
        dc=max((o.discovery_dc for o in hidden),default=15)
        illumination=self.geometry.illumination(position);darkvision=actor.darkvision_ft>0 and self.geometry.distance(actor.position.key(),position)<=actor.darkvision_ft
        check=self.rules.skill(actor.stats,'perception',dc,rng=self.rng,disadvantage=illumination=='dark' or (illumination=='dim' and not darkvision))
        actor.action=False;found=hidden if check['success'] else []
        for obj in found:obj.secret=False
        # A failed search must not reveal whether a secret exists or its difficulty.
        roll={k:v for k,v in check.items() if k not in ('dc','success')}
        return dict(kind='search',actor=actor_id,roll=roll,found=[o.id for o in found],text='Обнаружено: '+', '.join(o.name for o in found) if found else 'Нового прохода или объекта не обнаружено.')
    def fall_preview(self,actor_id,position):
        actor=self.actors[actor_id];self.assert_movement(actor)
        source=actor.position.key();target=tuple(position)
        if target[2]>=source[2] or max(abs(target[i]-source[i]) for i in range(2))>1:raise ValueError('Падение требует соседней нижней поверхности')
        if actor.team=='hero' and ','.join(str(n) for n in target) not in self.data.get('known_cells',{}):raise ValueError('Сначала установите поверхность приземления; падение в неизвестность не рассчитывается этой командой')
        if self.geometry.can_stand((target[0],target[1],source[2])):raise ValueError('Это обычная поверхность; сначала переместитесь к краю')
        if not self.geometry.can_stand(target,self.occupied(actor_id)):raise ValueError('Приземление в этой клетке невозможно')
        if any((target[0],target[1],z) in self.geometry.cells for z in range(target[2]+1,source[2])):raise ValueError('Между уровнями есть другая поверхность; выберите ближайшее приземление')
        if actor.movement_spent+5>self.speed(actor)+actor.dash_movement:raise ValueError('Недостаточно движения, чтобы шагнуть с края')
        height=(source[2]-target[2])*5;count=min(self.rules.preset.fall_max_dice,height//self.rules.preset.fall_die_feet)
        return dict(height_ft=height,dice_count=count,die=6,movement_cost_ft=5,warning=f'Падение с высоты {height} футов: {count}d6 дробящего урона и возможное падение ничком. Возможен серьёзный смертельный исход. Подтвердите действие отдельно.')
    def drop(self,actor_id,position):
        info=self.fall_preview(actor_id,position);actor=self.actors[actor_id];target=tuple(position)
        actor.movement_spent+=5;actor.position=Position(x=target[0],y=target[1],z=target[2])
        height,count=info['height_ft'],info['dice_count']
        dice=[self.rng.randint(1,6) for _ in range(count)];effect=self.damage(actor,sum(dice),'bludgeoning')
        if effect['damage_after_traits'] and 'prone' not in actor.stats.conditions:actor.stats.conditions.append('prone')
        return dict(kind='fall',actor=actor_id,height_ft=height,dice=dice,text=f'Падение с высоты {height} футов; урон {effect["hp_damage"]}.',**effect)
    def bonus(self,actor_id,kind):
        actor=self.actors[actor_id]
        if not self.data.get('active') or self.data.get('pending_death') or self.data.get('pending_reaction') or self.current().id!=actor.id:raise ValueError('Бонусное действие недоступно в этом ходе')
        if actor.surprised and not actor.first_turn_finished and self.rules.preset.surprise=='skip_first_turn':raise ValueError('Внезапность запрещает бонусное действие')
        if incapacitated(actor) or not actor.bonus_action:raise ValueError('Бонусное действие недоступно')
        if self.rules.preset.id!='dnd2024' or actor.stats.class_name.lower() not in ('rogue','плут') or actor.stats.level<2 or 'cunning_action' not in actor.stats.features:
            raise ValueError('Нужна явно зарегистрированная cunning_action плута 2+ уровня D&D 2024')
        if kind=='dash':actor.dash_movement+=self.speed(actor)
        elif kind=='disengage':actor.disengaged=True
        else:raise ValueError('Эта часть способности не реализована')
        actor.bonus_action=False
        return dict(kind=kind,actor=actor_id,economy='bonus_action',feature='cunning_action')
    def assert_movement(self,actor):
        if not self.data.get('active') or self.data.get('pending_death') or self.data.get('pending_reaction'):raise ValueError('Бой остановлен до решения')
        if self.current().id!=actor.id:raise ValueError('Сейчас ход другого участника')
        if not self.speed(actor):raise ValueError('В этом состоянии движение недоступно')
        if actor.surprised and not actor.first_turn_finished and self.rules.preset.surprise=='skip_first_turn':raise ValueError('Внезапность не позволяет двигаться в первом ходу')
    def movement_path(self,actor_id,target):
        actor=self.actors[actor_id];self.assert_movement(actor)
        remaining=self.speed(actor)+actor.dash_movement-actor.movement_spent
        occupied={a.position.key() for a in self.actors.values() if a.id!=actor_id and not a.dead and self.sees(actor,a)}
        geometry=self.geometry
        if actor.team=='hero' and self.data.get('known_cells'):
            perceived={tuple(s[k] for k in ('x','y','z')):s.copy() for s in self.data['known_cells'].values()}
            if tuple(target) not in perceived:
                origin=actor.position.key();known=perceived.get(origin,{})
                is_link=any(tuple(p[k] for k in ('x','y','z'))==tuple(target) for p in known.get('links',[]))
                adjacent=target[2]==origin[2] and max(abs(target[i]-origin[i]) for i in range(2))==1
                if not (is_link or adjacent):raise ValueError('Сначала исследуйте соседнюю поверхность; дальний путь в неизвестность не строится')
                perceived[tuple(target)]=Cell(x=target[0],y=target[1],z=target[2]).model_dump()
            for snap in perceived.values():snap['links']=[p for p in snap.get('links',[]) if tuple(p[k] for k in ('x','y','z')) in perceived]
            geometry=Geometry(BattleMap(width=self.map.width,height=self.map.height,cells=list(perceived.values())),self.rules.preset)
        path,cost=geometry.path(actor.position.key(),target,occupied=occupied,budget=remaining/(2 if 'prone' in actor.stats.conditions else 1),diagonals=actor.diagonals)
        source=self.actors.get(actor.condition_sources.get('frightened'))
        if source and self.sees(actor,source) and any(self.geometry.distance(b,source.position.key())<self.geometry.distance(a,source.position.key()) for a,b in zip(path,path[1:])):raise ValueError('Испуг не позволяет приблизиться к источнику страха')
        return path,cost*(2 if 'prone' in actor.stats.conditions else 1)
    def move(self,actor_id,target):
        path,_=self.movement_path(actor_id,target)
        return self._follow_path(actor_id,path)
    def _follow_path(self,actor_id,path,ignored=()):
        actor=self.actors[actor_id];results=[]
        for index in range(1,len(path)):
            source,dest=tuple(path[index-1]),tuple(path[index]);handled=set(ignored) if index==1 else set()
            if not self.geometry.can_stand(dest,self.occupied(actor_id)):
                results.append(dict(kind='movement_stop',actor=actor_id,text='Путь остановлен препятствием.'));break
            cost=self.geometry.edge_cost(source,dest,actor.diagonals)*(2 if 'prone' in actor.stats.conditions else 1)
            if actor.movement_spent+cost>self.speed(actor)+actor.dash_movement:
                results.append(dict(kind='movement_stop',actor=actor_id,text='Движение остановлено: фактическая стоимость поверхности превышает оставшееся движение.'));break
            if not actor.disengaged:
                for foe in sorted(self.actors.values(),key=lambda a:a.id):
                    reach=5 if WEAPONS[foe.weapon].ranged else WEAPONS[foe.weapon].reach_ft
                    if foe.id in handled or not hostile(foe,actor) or incapacitated(foe) or not foe.reaction or not self.sees(foe,actor):continue
                    if 'charmed' in foe.stats.conditions and foe.condition_sources.get('charmed')==actor.id:continue
                    if self.geometry.distance(foe.position.key(),source)<=reach<self.geometry.distance(foe.position.key(),dest):
                        handled.add(foe.id)
                        if foe.team=='hero':
                            self.data['pending_reaction']=dict(reactor=foe.id,target=actor_id,path=[list(p) for p in path[index-1:]],ignored=sorted(handled))
                            results.append(dict(kind='reaction_pending',actor=foe.id,target=actor_id));return results
                        results.append(self.attack(foe.id,actor_id,reaction=True))
                        if incapacitated(actor) or self.data.get('pending_death'):return results
            actor.movement_spent+=cost;actor.diagonals+=int(sum(source[i]!=dest[i] for i in range(3))>1)
            actor.position=Position(x=dest[0],y=dest[1],z=dest[2])
            results.append(dict(kind='move_step',actor=actor_id,position=actor.position.model_dump(),cost_ft=cost))
            from .combat_view import refresh_vision
            if actor.team=='hero':refresh_vision(self)
            else:
                hero=next(a for a in self.actors.values() if a.team=='hero')
                if self.sees(hero,actor):self.data.setdefault('last_seen',{})[actor.id]=dict(id=actor.id,name=actor.stats.name,position=actor.position.model_dump(),round=self.data['round'])
        return results
    def resolve_reaction(self,choice):
        pending=self.data.get('pending_reaction')
        if not pending:raise ValueError('Нет ожидающей реакции')
        if choice not in ('attack','decline'):raise ValueError('Выберите атаку или отказ от реакции')
        self.data['pending_reaction']=None;results=[]
        if choice=='attack':results.append(self.attack(pending['reactor'],pending['target'],reaction=True))
        mover=self.actors[pending['target']]
        if not incapacitated(mover) and not self.data.get('pending_death'):results+=self._follow_path(mover.id,pending['path'],pending['ignored'])
        return results
    def lethal(self,actor,cause):
        mechanical=dict(actor=actor.id,cause=cause,death_failures=actor.death_failures,hp=actor.stats.hp)
        if actor.team=='hero' and not self.data.get('lethal_risk_declared'):
            self.data['pending_death']=mechanical
            return dict(mechanical_death=True,pending_user_decision=True)
        actor.dead=True
        if actor.team=='hero':self.state['hero_death']={**mechanical,'risk_announced_before':True}
        return dict(mechanical_death=True,pending_user_decision=False)
    def damage(self,target,amount,type,*,critical=False,nonlethal=False):
        if amount<0:raise ValueError('Урон не может быть отрицательным')
        raw=amount
        petrified='petrified' in target.stats.conditions
        if type in target.immunities or (petrified and type=='poison'):amount=0
        else:
            if type in target.resistances or petrified:amount//=2
            if type in target.vulnerabilities:amount*=2
        shielded=min(target.temp_hp,amount);target.temp_hp-=shielded;amount-=shielded
        hp_before=target.stats.hp;max_hp=self.rules.exhaustion_effects(target.stats)['effective_max_hp'];outcome={}
        target.stats.hp=max(0,hp_before-amount)
        if amount and target.stats.hp==0:
            if nonlethal and hp_before>0:target.stable=True
            elif hp_before==0:
                target.stable=False;target.death_failures=min(3,target.death_failures+(2 if critical else 1))
                if amount>=max_hp or target.death_failures>=3:outcome=self.lethal(target,'damage_at_zero_hp')
            elif amount-hp_before>=max_hp:outcome=self.lethal(target,'massive_damage')
            elif target.team!='hero':outcome=self.lethal(target,'zero_hp')
            if 'unconscious' not in target.stats.conditions:target.stats.conditions.append('unconscious')
            if 'prone' not in target.stats.conditions:target.stats.conditions.append('prone')
        return dict(raw_damage=raw,damage_after_traits=amount+shielded,temp_hp_absorbed=shielded,hp_damage=amount,hp_before=hp_before,hp_after=target.stats.hp,damage_type=type,**outcome)
    def attack(self,actor_id,target_id,*,reaction=False,nonlethal=False):
        actor=self.actors[actor_id];target=self.actors[target_id];self.assert_action(actor,reaction=reaction)
        if not hostile(actor,target) or target.dead:raise ValueError('Недопустимая цель атаки')
        weapon=WEAPONS['unarmed' if reaction and WEAPONS[actor.weapon].ranged else actor.weapon]
        if nonlethal and weapon.ranged:raise ValueError('Нелетальное добивание поддержано только в ближнем бою')
        distance=self.geometry.distance(actor.position.key(),target.position.key())
        cover=self.geometry.cover(actor.position.key(),target.position.key())
        if cover==100:raise ValueError('Цель закрыта полным укрытием')
        if distance>(weapon.long_range_ft if weapon.ranged else weapon.reach_ft):raise ValueError('Цель вне дистанции оружия')
        if not self.sees(actor,target):raise ValueError('Цель сейчас не видна; выберите известное место или уточните намерение')
        if weapon.ranged and not actor.ammo:raise ValueError('Нет боеприпасов')
        if 'charmed' in actor.stats.conditions and actor.condition_sources.get('charmed')==target.id:raise ValueError('Нельзя атаковать источник очарования')
        conditions=set(target.stats.conditions);mine=set(actor.stats.conditions)
        advantage=not self.sees(target,actor) or bool(conditions&{'blinded','paralyzed','restrained','stunned','unconscious'}) or ('prone' in conditions and distance<=5)
        disadvantage=bool(mine&{'blinded','prone','restrained','poisoned'}) or ('prone' in conditions and distance>5) or (target.dodging and self.sees(target,actor) and self.speed(target)>0)
        if 'frightened' in mine:
            source=self.actors.get(actor.condition_sources.get('frightened'))
            disadvantage=disadvantage or bool(source and self.sees(actor,source))
        if weapon.ranged:
            disadvantage=disadvantage or distance>weapon.range_ft or any(hostile(a,actor) and not incapacitated(a) and self.geometry.distance(actor.position.key(),a.position.key())<=5 and self.sees(a,actor) for a in self.actors.values())
        result=self.rules.d20(actor.stats,weapon.ability,kind='attack',proficient=weapon.proficiency=='all' or weapon.proficiency in actor.stats.weapon_proficiencies or weapon.id in actor.stats.specific_weapon_proficiencies,advantage=advantage,disadvantage=disadvantage,rng=self.rng)
        hit=result['selected']!=1 and (result['selected']>=self.rules.preset.crit_threshold or result['total']>=target.stats.ac+cover)
        critical=hit and (result['selected']>=self.rules.preset.crit_threshold or (distance<=5 and bool(conditions&{'paralyzed','unconscious'})))
        if reaction:actor.reaction=False
        else:actor.action=False
        if weapon.ranged:
            if actor.team=='hero' and self.state.get('economy'):
                from .inventory import InventoryEngine
                InventoryEngine(self.state).spend_arrow()
            actor.ammo-=1
        damage_dice=[self.rng.randint(1,weapon.die) for _ in range(weapon.dice_count*(2 if critical else 1))] if hit else []
        effect=self.damage(target,max(0,sum(damage_dice)+weapon.flat+(actor.stats.abilities[weapon.ability]-10)//2),weapon.damage_type,critical=critical,nonlethal=nonlethal) if hit else {}
        return dict(kind='attack',actor=actor_id,target=target_id,weapon=weapon.id,distance_ft=distance,cover_bonus=cover,roll=result,hit=hit,critical=critical,damage_dice=damage_dice,**effect)
    def speed(self,actor):
        if incapacitated(actor) or set(actor.stats.conditions)&{'grappled','restrained'}:return 0
        speed=self.rules.exhaustion_effects(actor.stats)['speed']
        if actor.team=='hero' and self.state.get('economy'):
            from .inventory import InventoryEngine
            inventory=InventoryEngine(self.state)
            if inventory.weight()>inventory.capacity():return 0
            speed=max(0,speed-inventory.speed_penalty())
        return speed
    def death_save(self,actor):
        if actor.stats.hp or actor.stable or actor.dead:return None
        effects=self.rules.exhaustion_effects(actor.stats,'save')
        dice=[self.rng.randint(1,20) for _ in range(2 if effects['disadvantage'] else 1)]
        roll=min(dice);total=roll+effects['penalty'];outcome={}
        if roll==20:
            actor.stats.hp=1;actor.death_successes=actor.death_failures=0
            actor.stats.conditions=[c for c in actor.stats.conditions if c!='unconscious']
        elif roll==1:actor.death_failures=min(3,actor.death_failures+2)
        elif total<10:actor.death_failures=min(3,actor.death_failures+1)
        else:actor.death_successes=min(3,actor.death_successes+1)
        if actor.death_failures>=3:outcome=self.lethal(actor,'death_saves')
        if actor.death_successes>=3:actor.stable=True;actor.death_successes=actor.death_failures=0
        return dict(kind='death_save',actor=actor.id,die=roll,dice=dice,total=total,successes=actor.death_successes,failures=actor.death_failures,stable=actor.stable,**outcome)
