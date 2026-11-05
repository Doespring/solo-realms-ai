"""Versioned, declarative SRD subset. No eval, imports, executable expressions or AI rules."""
import copy,hashlib,json,secrets,time,uuid
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Strict,Hero,Step

SKILLS=dict(acrobatics='dexterity',animal_handling='wisdom',arcana='intelligence',athletics='strength',deception='charisma',history='intelligence',insight='wisdom',intimidation='charisma',investigation='intelligence',medicine='wisdom',nature='intelligence',perception='wisdom',performance='charisma',persuasion='charisma',religion='intelligence',sleight_of_hand='dexterity',stealth='dexterity',survival='wisdom')
SKILL_NAMES=dict(zip(SKILLS,['Акробатика','Уход за животными','Магия','Атлетика','Обман','История','Проницательность','Запугивание','Анализ','Медицина','Природа','Восприятие','Выступление','Убеждение','Религия','Ловкость рук','Скрытность','Выживание']))
SOURCES={'2014':'https://media.wizards.com/2023/downloads/dnd/SRD_CC_v5.1.pdf','2024':'https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf'}

class Ruleset(Strict):
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,79}$')
    name: str = Field(min_length=1,max_length=120)
    edition: Literal['2014','2024','custom'] = 'custom'
    version: int = Field(default=1,ge=1,le=1000000)
    proficiency_by_level: list[int] = Field(default_factory=lambda:[2+(i//4) for i in range(20)],min_length=20,max_length=20)
    exhaustion: Literal['2014','2024','none'] = '2024'
    surprise: Literal['skip_first_turn','initiative_disadvantage','none'] = 'initiative_disadvantage'
    long_rest_hit_dice: Literal['half','all'] = 'all'
    potion_action: Literal['action','bonus'] = 'bonus'
    diagonal: Literal['five_feet','alternating','euclidean'] = 'five_feet'
    cell_feet: Literal[5] = 5
    crit_threshold: int = Field(default=20,ge=2,le=20)
    carry_multiplier: int = Field(default=15,ge=1,le=100)
    fall_die_feet: int = Field(default=10,ge=5,le=50)
    fall_max_dice: int = Field(default=20,ge=1,le=40)
    travel_pace_ft: dict[Literal['slow','normal','fast'],int] = Field(default_factory=lambda:dict(slow=200,normal=300,fast=400))
    requires_implementation: list[str] = Field(default_factory=list,max_length=100)
    notes: str = Field(default='',max_length=3000)
    @model_validator(mode='after')
    def bounded(self):
        if any(not 0<=n<=20 for n in self.proficiency_by_level):raise ValueError('Неверная таблица владения')
        if any(len(n)>300 for n in self.requires_implementation):raise ValueError('Слишком длинная неподдержанная механика')
        if set(self.travel_pace_ft)!={'slow','normal','fast'} or any(not 1<=n<=2000 for n in self.travel_pace_ft.values()):raise ValueError('Неверные темпы путешествия')
        return self

BUILTINS={
 'dnd2014':Ruleset(id='dnd2014',name='D&D 2014 · часть SRD 5.1',edition='2014',exhaustion='2014',surprise='skip_first_turn',long_rest_hit_dice='half',potion_action='action'),
 'dnd2024':Ruleset(id='dnd2024',name='D&D 2024 · часть SRD 5.2.1',edition='2024'),
 'custom':Ruleset(id='custom',name='Пользовательский · базовый',notes='Безопасные параметры зарегистрированных механик; неполная система.')}

def active(state):
    # Old campaigns resolve to the same 2024 check rules without rewriting old JSON.
    return Ruleset.model_validate(state['ruleset']) if 'ruleset' in state else BUILTINS['dnd2024'].model_copy(deep=True)

def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

class RulesetEngine:
    def __init__(self,preset):self.preset=preset
    def proficiency(self,level):return self.preset.proficiency_by_level[level-1]
    def exhaustion_effects(self,hero,kind='check'):
        level=hero.exhaustion;mode=self.preset.exhaustion
        return dict(penalty=-2*level if mode=='2024' else 0,
            disadvantage=mode=='2014' and level>=(1 if kind in ('check','initiative') else 3),
            speed=0 if mode=='2014' and level>=5 else hero.speed_ft//2 if mode=='2014' and level>=2 else max(0,hero.speed_ft-5*level) if mode=='2024' else hero.speed_ft,
            effective_max_hp=hero.max_hp//2 if mode=='2014' and level>=4 else hero.max_hp,
            mechanical_death=mode!='none' and level>=6)
    def d20(self,hero,ability,*,kind='check',proficient=False,expertise=False,advantage=False,disadvantage=False,rng=None):
        rng=rng or secrets.SystemRandom();effects=self.exhaustion_effects(hero,kind)
        if effects['mechanical_death']:raise ValueError('Механически смертельное истощение требует отдельного решения о последствиях')
        disadvantage=disadvantage or effects['disadvantage'] or ('poisoned' in hero.conditions and kind in ('check','attack'))
        mode='обычный' if advantage==disadvantage else 'преимущество' if advantage else 'помеха'
        dice=[rng.randint(1,20) for _ in range(1 if mode=='обычный' else 2)]
        selected=max(dice) if mode=='преимущество' else min(dice) if mode=='помеха' else dice[0]
        bonus=(hero.abilities[ability]-10)//2+(self.proficiency(hero.level)*(2 if expertise else 1) if proficient else 0)+effects['penalty']
        return dict(dice=dice,selected=selected,bonus=bonus,total=selected+bonus,mode=mode,ruleset={'id':self.preset.id,'version':self.preset.version},source=SOURCES.get(self.preset.edition,'Пользовательский пресет'))
    def check(self,hero,step,rng=None):
        if step.skill:
            if step.dc is None:raise ValueError('Для навыка нужна Сл')
            result=self.skill(hero,step.skill,step.dc,rng=rng,advantage=step.advantage,disadvantage=step.disadvantage)
            return {**result,'formula':f"d20 {result['bonus']:+d} = {result['total']} против Сл {step.dc}",'ruling':'Владение и экспертиза взяты из подтверждённого листа'}
        if step.ability is None or step.dc is None:raise ValueError('Для проверки нужны характеристика и Сл')
        result=self.d20(hero,step.ability,proficient=step.proficient,advantage=step.advantage,disadvantage=step.disadvantage,rng=rng)
        return dict(type='check',ability=step.ability,**result,dc=step.dc,success=result['total']>=step.dc,
            formula=f"d20 {result['bonus']:+d} = {result['total']} против Сл {step.dc}",ruling='Характеристика, Сл и владение явно подтверждены игроком')
    def skill(self,hero,skill,dc,*,rng=None,advantage=False,disadvantage=False):
        if skill not in SKILLS:raise ValueError('Неизвестный навык')
        result=self.d20(hero,SKILLS[skill],proficient=skill in hero.skill_proficiencies,expertise=skill in hero.expertise,rng=rng,advantage=advantage,disadvantage=disadvantage)
        return dict(type='check',skill=skill,ability=SKILLS[skill],dc=dc,success=result['total']>=dc,**result)
    def initiative(self,hero,*,surprised=False,rng=None):
        result=self.d20(hero,'dexterity',kind='initiative',disadvantage=surprised and self.preset.surprise=='initiative_disadvantage',rng=rng)
        return dict(**result,skip_first_turn=surprised and self.preset.surprise=='skip_first_turn')
    def saving_throw(self,hero,ability,dc,*,rng=None,advantage=False,disadvantage=False):
        result=self.d20(hero,ability,kind='save',proficient=ability in hero.saving_throw_proficiencies,rng=rng,advantage=advantage,disadvantage=disadvantage)
        return dict(type='save',ability=ability,dc=dc,success=result['total']>=dc,**result)
    def sheet(self,hero):
        effects=self.exhaustion_effects(hero)
        return dict(hero=hero.model_dump(),ruleset=self.preset.model_dump(),proficiency=self.proficiency(hero.level),
            skills={k:dict(name=SKILL_NAMES[k],ability=v,bonus=(hero.abilities[v]-10)//2+(self.proficiency(hero.level)*(2 if k in hero.expertise else 1) if k in hero.skill_proficiencies else 0)+effects['penalty']) for k,v in SKILLS.items()},
            initiative_bonus=(hero.abilities['dexterity']-10)//2+effects['penalty'],speed_ft=effects['speed'],effective_max_hp=effects['effective_max_hp'],
            hit_dice_available=hero.level if hero.hit_dice_available is None else hero.hit_dice_available,
            coverage=['ability_checks','skills','proficiency','exhaustion','initiative','rest'],
            unsupported=['automatic_class_progression','multiclass','feats_effects','spell_catalog','automatic_origins','equipment_effects','full_srd_combat_actions'],
            mechanics_notes='Вид, класс, происхождение, способности и заклинания записываются вручную. Их название само по себе не даёт механических эффектов.')
    def rest(self,state,kind,*,hit_dice=0,food_water=False,rng=None):
        hero=Hero.model_validate(state['hero']);rng=rng or secrets.SystemRandom()
        if state.get('hero_death'):raise ValueError('Необратимая смерть не отменяется отдыхом; доступен откат или контрольная точка')
        if self.exhaustion_effects(hero)['mechanical_death']:raise ValueError('Механически смертельный исход нельзя заменить отдыхом')
        if state.get('combat',{}).get('active'):raise ValueError('Нельзя отдыхать во время боя')
        if hero.hp==0:raise ValueError('Отдых с нулевыми хитами требует отдельного разрешения правил')
        now=state.get('elapsed_minutes',0);available=hero.level if hero.hit_dice_available is None else hero.hit_dice_available
        dice=[]
        if kind=='short':
            if not 0<=hit_dice<=available:raise ValueError('Недостаточно костей хитов')
            dice=[rng.randint(1,hero.hit_die) for _ in range(hit_dice)]
            healing=sum(max(1 if self.preset.edition=='2024' else 0,n+(hero.abilities['constitution']-10)//2) for n in dice)
            hero.hp=min(self.exhaustion_effects(hero)['effective_max_hp'],hero.hp+healing)
            hero.hit_dice_available=available-hit_dice;duration=60
        elif kind=='long':
            if now+480-state.get('last_long_rest',-1440)<1440:raise ValueError('Длительный отдых уже был за последние 24 часа')
            hero.hit_dice_available=min(hero.level,available+(hero.level if self.preset.long_rest_hit_dice=='all' else max(1,hero.level//2)))
            if hero.exhaustion and (self.preset.exhaustion=='2024' or food_water):hero.exhaustion-=1
            hero.hp=self.exhaustion_effects(hero)['effective_max_hp'];duration=480;state['last_long_rest']=now+duration
        else:raise ValueError('Неизвестный отдых')
        for resource in hero.resources.values():
            if resource.recovery==kind or (kind=='long' and resource.recovery=='short'):resource.current=resource.maximum
        state['hero']=hero.model_dump();state['elapsed_minutes']=now+duration
        return dict(type='scripted',kind='rest',rest=kind,dice=dice,duration_minutes=duration,ruleset={'id':self.preset.id,'version':self.preset.version},text='Отдых завершён. Ресурсы восстановлены по выбранному пресету.')

class RulesetRepository:
    def __init__(self,store):self.store=store
    def list(self):
        with self.store.connection() as c:
            return [json.loads(r[0]) for r in c.execute('SELECT body FROM ruleset_versions v WHERE version=(SELECT MAX(version) FROM ruleset_versions WHERE id=v.id) ORDER BY id')]
    def get(self,id,version=None):
        with self.store.connection() as c:
            row=c.execute('SELECT body FROM ruleset_versions WHERE id=?'+(' AND version=?' if version else '')+' ORDER BY version DESC LIMIT 1',(id,version) if version else (id,)).fetchone()
            if not row:raise KeyError('Набор правил не найден')
            return Ruleset.model_validate_json(row[0])
    def save(self,body):
        if body.id in BUILTINS:raise ValueError('Встроенный пресет неизменяем; создайте копию с новым id')
        if body.edition!='custom':raise ValueError('Изменённые правила должны обозначаться как пользовательские')
        with self.store.connection(True) as c:
            version=c.execute('SELECT COALESCE(MAX(version),0)+1 FROM ruleset_versions WHERE id=?',(body.id,)).fetchone()[0]
            saved=body.model_copy(update={'version':version})
            c.execute('INSERT INTO ruleset_versions VALUES(?,?,?,?)',(saved.id,version,saved.model_dump_json(),time.time()))
        return saved
    def history(self,id):
        with self.store.connection() as c:return [json.loads(r[0]) for r in c.execute('SELECT body FROM ruleset_versions WHERE id=? ORDER BY version DESC',(id,))]
    def preview(self,campaign,id,version=None):
        target=self.get(id,version);current=self.store.campaign(campaign);hero=Hero.model_validate(current['state']['hero']);old=RulesetEngine(active(current['state']));new=RulesetEngine(target)
        changes=[dict(field=field,before=old.exhaustion_effects(hero)[field],after=new.exhaustion_effects(hero)[field]) for field in ['penalty','disadvantage','speed','effective_max_hp'] if old.exhaustion_effects(hero)[field]!=new.exhaustion_effects(hero)[field]]
        for field in ['proficiency','skills']:
            before=old.sheet(hero)[field];after=new.sheet(hero)[field]
            if before!=after:changes.append(dict(field=field,before=before,after=after))
        for field in ['surprise','long_rest_hit_dice','potion_action','diagonal','crit_threshold','carry_multiplier']:
            before=getattr(old.preset,field);after=getattr(new.preset,field)
            if before!=after:changes.append(dict(field=field,before=before,after=after))
        warnings=['Классы, черты и заклинания не конвертируются автоматически; их названия не являются реализованными эффектами.']
        if hero.features or hero.spells:warnings.append('Записанные вручную способности и заклинания сохраняются без изменения; проверьте их совместимость.')
        if current['state'].get('combat',{}).get('active'):raise ValueError('Завершите бой до смены набора правил')
        if new.exhaustion_effects(hero)['mechanical_death']:raise ValueError('Переход вызовет смертельный исход; требуется отдельное решение о риске')
        after_hero=hero.model_copy(deep=True);after_hero.hp=min(hero.hp,new.exhaustion_effects(hero)['effective_max_hp'])
        token=str(uuid.uuid4());payload=dict(target=target.model_dump(),hero=after_hero.model_dump())
        with self.store.connection(True) as c:
            c.execute('DELETE FROM ruleset_conversions WHERE expires<?',(time.time(),))
            c.execute('INSERT INTO ruleset_conversions VALUES(?,?,?,?,?,?)',(token,campaign,current['version'],digest(current['state']),json.dumps(payload),time.time()+600))
        return dict(approval=token,changes=changes,warnings=warnings,target=target.model_dump(),before_hero=hero.model_dump(),after_hero=after_hero.model_dump(),expires_seconds=600)
    def apply(self,campaign,token,confirm):
        if not confirm:raise ValueError('Требуется явное согласие с последствиями перехода')
        # All authorization checks, checkpoint and journal mutation share one transaction.
        with self.store.connection(True) as c:
            row=c.execute('SELECT * FROM ruleset_conversions WHERE token=? AND campaign=?',(token,campaign)).fetchone()
            if not row:
                old=c.execute('SELECT id FROM actions WHERE campaign=? AND request_id=?',(campaign,'ruleset:'+token)).fetchone()
                if old:
                    event=self.store.event_for_action(old['id'],c)
                    if event['undone']:raise ValueError('Переход отменён; подготовьте новый предпросмотр')
                    return dict(event=event,checkpoint=event['results'][0]['checkpoint'])
            if not row or row['expires']<time.time():raise ValueError('Предпросмотр перехода отсутствует или истёк')
            current=self.store.campaign(campaign,c)
            if current['version']!=row['base_version'] or digest(current['state'])!=row['state_hash']:raise ValueError('Состояние изменилось; подготовьте новый предпросмотр')
            payload=json.loads(row['payload']);checkpoint=str(uuid.uuid4())
            active_events=[r[0] for r in c.execute('SELECT id FROM events WHERE campaign=? AND undone=0',(campaign,))]
            seq=c.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE campaign=?',(campaign,)).fetchone()[0]
            c.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)',(checkpoint,campaign,'Перед сменой правил',json.dumps(current['state'],ensure_ascii=False),seq,time.time(),json.dumps(active_events)))
            # Reuse journal code with this connection; no competing state table.
            event=self.store.engine_event(c,current,'ruleset:'+token,'Смена набора правил',lambda state:(state.update(ruleset=payload['target'],hero=payload['hero']) or [dict(type='scripted',kind='ruleset_conversion',text='Набор правил изменён после согласования',ruleset=payload['target'],checkpoint=checkpoint)]))
            c.execute('DELETE FROM ruleset_conversions WHERE token=?',(token,))
        return dict(event=event,checkpoint=checkpoint)
