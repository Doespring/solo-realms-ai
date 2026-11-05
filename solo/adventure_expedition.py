"""Scoped, source-reviewed survival and wilderness procedures. No global house rules."""
import copy
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Strict,Hero
from .adventure_contracts import Provenance,Predicate,SurvivalRule,Companion,WildernessProcedure

def validate_rules(state,definition):
    for rule in definition.survival:
        if rule.source.review!='confirmed':raise ValueError('Правило экспедиции не сверено')
        if rule.kind in ('food','water') and rule.supply_resource not in state['hero'].get('resources',{}):raise ValueError('Запасы экспедиции должны ссылаться на подтверждённый ресурс листа')
        if rule.kind=='forage' and rule.gain_resource not in state['hero'].get('resources',{}):raise ValueError('Результат поиска должен ссылаться на подтверждённый ресурс листа')
        if rule.tracker and rule.tracker not in {t.id for t in definition.trackers}:raise ValueError('Неизвестный счётчик угроз')
    names={n['name'] for p in state['locations'] for n in p.get('npc',[])}
    for actor in definition.companions:
        if actor.source.review!='confirmed' or actor.npc not in names:raise ValueError('Спутник должен быть зарегистрирован в проверенном мире')
    for procedure in definition.wilderness:
        if procedure.source.review!='confirmed' or procedure.encounter_table not in {t.id for t in definition.tables}:raise ValueError('Процедура встреч не проверена')

def plan_day(state,procedure,table,rng=None):
    from .adventure_engine import roll
    data=state['adventure'];now=state.get('elapsed_minutes',0);day=int(now//1440);key=procedure.id+':'+str(day)
    if key in data.setdefault('wilderness_days',{}):return
    count=roll('1d8',rng);periods=[];rolls=[count]
    if count['total']<=4:periods=[count['total']-1]
    elif count['total']<=6:
        for _ in range(2):choice=roll('1d4',rng);rolls.append(choice);periods.append(choice['total']-1)
    planned=[]
    for index,period in enumerate(periods):
        window=procedure.period_windows[period];when=day*1440+(window[0]+window[1])/2
        if when<now:continue # Past windows are not retroactively played as new encounters.
        encounter=roll(table.formula,rng);row=next(r for r in table.rows if r.low<=encounter['total']<=r.high)
        weather=roll('1d20+1',rng);storm=weather['total']>encounter['total'];before=after=None
        if storm:before=roll('1d4',rng);after=roll('1d4',rng)
        planned.append(dict(id=key+':'+str(index),when=when,table=table.id,row_index=table.rows.index(row),encounter=encounter,weather=weather,storm=storm,storm_start=when-before['total']*60 if before else None,storm_after=after['total']*60 if after else None,storm_rolls=[before,after] if storm else [],resolved=False,ending=None))
    data['wilderness_days'][key]=dict(source=procedure.source.model_dump(),count_rolls=rolls,periods=periods,planned=planned,policy='Explicit optional daily dice procedure; midpoint chosen by reviewed GM settings')

def tick_expedition(state,definition,rng=None):
    from .adventure_engine import satisfied,random_source,roll
    from .rulesets import RulesetEngine,active
    data=state['adventure'];now=state.get('elapsed_minutes',0);messages=[]
    if data.get('pending_hazard'):return messages
    tables={t.id:t for t in definition.tables}
    for procedure in definition.wilderness:
        if procedure.enabled and all(satisfied(state,c) for c in procedure.conditions):plan_day(state,procedure,tables[procedure.encounter_table],rng)
    for day in data.get('wilderness_days',{}).values():
        for encounter in day['planned']:
            if encounter['storm'] and encounter['storm_start']<=now and (encounter['ending'] is None or now<encounter['ending']):
                data['weather']=dict(kind='blizzard',description='Метель',public=True,observed_at=now)
            elif encounter['ending'] is not None and now>=encounter['ending'] and (data.get('weather') or {}).get('kind')=='blizzard':
                data['weather']=None
            if encounter['resolved'] or encounter['when']>now:continue
            table=tables.get(encounter['table'])
            if table is None:continue
            data.setdefault('pending_wilderness',[]).append(dict(id=encounter['id'],row=table.rows[encounter['row_index']].label,source=day['source'],location=state['location'],status='requires_review_of_encounter_mechanics'))
            encounter['resolved']=True
            data['journal'].append(dict(id=encounter['id'],category='random',source=day['source'],game_time=now,rolls=[encounter['encounter'],encounter['weather'],*encounter['storm_rolls']],status='scheduled_encounter_requires_mechanical_resolution'))
            # This does not invent a statblock, an attack, a victory or an encounter duration.
            messages.append(dict(kind='encounter',text='Сценарная процедура определила встречу. Неизвестная механика не заменяется предположением.'))
    for rule in definition.survival:
        if rule.kind in ('forage','navigation'):continue # These require a chosen player action.
        due=data.setdefault('survival_due',{}).get(rule.id,data.get('started_at',0)+rule.interval_minutes)
        if not all(satisfied(state,c) for c in rule.conditions) or rule.immunity_flag and data['flags'].get(rule.immunity_flag) is True:
            if due<=now:due+= (int((now-due)//rule.interval_minutes)+1)*rule.interval_minutes
            data['survival_due'][rule.id]=due
            continue
        loops=0
        while due<=now+1e-8:
            loops+=1
            if loops>48:raise ValueError('Разбейте длительный шаг времени: слишком много проверок экспедиции')
            hero=Hero.model_validate(state['hero']);record=dict(id=rule.id,source=rule.source.model_dump(),game_time=due,rolls=[],status='resolved')
            failed=False
            if rule.kind in ('food','water'):
                resource=hero.resources[rule.supply_resource];people=1+sum(c.get('status')=='joined' for c in data.get('companions',{}).values());needed=people*rule.supply_per_person
                used=min(needed,resource.current);resource.current-=used;failed=used<needed;record.update(required=needed,used=used)
            else:
                rules=RulesetEngine(active(state));result=rules.d20(hero,rule.ability,kind='save' if rule.saving_throw else 'check',proficient=rule.saving_throw and rule.ability in hero.saving_throw_proficiencies,rng=random_source(rng));failed=result['total']<rule.dc;record['rolls'].append(dict(**result,dc=rule.dc,success=not failed))
            if failed and rule.tracker:
                tracker=next(t for t in definition.trackers if t.id==rule.tracker)
                data['trackers'][rule.tracker]=max(tracker.minimum,min(tracker.maximum,data['trackers'].get(rule.tracker,0)+rule.failure_delta))
            if failed and rule.failure_exhaustion:
                proposed=hero.model_copy(update={'exhaustion':min(6,hero.exhaustion+rule.failure_exhaustion)})
                if RulesetEngine(active(state)).exhaustion_effects(proposed)['mechanical_death']:
                    data['pending_hazard']=dict(kind='exhaustion',proposed=proposed.exhaustion,source=rule.source.model_dump(),mechanical_result='death_threshold',irreversible_transition='held');record['status']='requires_risk_resolution'
                else:hero=proposed
            state['hero']=hero.model_dump();data['journal'].append(record);due+=rule.interval_minutes
            messages.append(dict(kind='survival',text='Подтверждённая проверка экспедиции выполнена; ресурсы и последствия учтены.'))
            if data.get('pending_hazard'):break
        data['survival_due'][rule.id]=due
    for actor in definition.companions:
        current=data['companions'][actor.id]
        if current['status']=='joined' and actor.departure_conditions and all(satisfied(state,c) for c in actor.departure_conditions):
            current['status']='left';messages.append(dict(kind='companion',text=actor.npc+' покидает экспедицию по подтверждённым условиям.'))
    return messages

def forage(state,rule,rng=None):
    from .adventure_engine import satisfied,random_source,roll
    from .rulesets import active,RulesetEngine
    if not all(satisfied(state,c) for c in rule.conditions):raise ValueError('Условия поиска ресурсов сейчас не выполняются')
    hero=Hero.model_validate(state['hero']);result=RulesetEngine(active(state)).d20(hero,rule.ability,rng=random_source(rng));success=result['total']>=rule.dc;gain=0;gained=None
    if success:
        gained=roll(rule.gain_formula,rng);gain=max(0,gained['total']+((hero.abilities[rule.ability]-10)//2 if rule.add_ability_modifier else 0));resource=hero.resources[rule.gain_resource];resource.current=min(resource.maximum,resource.current+gain)
    state['hero']=hero.model_dump();state['elapsed_minutes']=state.get('elapsed_minutes',0)+rule.action_minutes
    state['adventure']['journal'].append(dict(id=rule.id,source=rule.source.model_dump(),game_time=state['elapsed_minutes'],rolls=[result,*([gained] if gained else [])],success=success,gain=gain,status='resolved'))
    return [dict(type='scripted',kind='forage',text='Поиск ресурсов '+('завершён успешно.' if success else 'не дал результата.'),success=success,gain=gain)]

def finish_encounter(state,id):
    """Explicit GM confirmation of an already played encounter, never an automatic victory."""
    data=state['adventure'];pending=next((e for e in data.get('pending_wilderness',[]) if e['id']==id),None)
    if pending is None or pending['status']=='completed':raise ValueError('Ожидающая встреча не найдена или уже завершена')
    encounter=next((e for day in data.get('wilderness_days',{}).values() for e in day['planned'] if e['id']==id),None)
    if encounter is None or not encounter['resolved']:raise ValueError('Встреча ещё не наступила')
    pending['status']='completed';encounter['ending']=state.get('elapsed_minutes',0)+(encounter['storm_after'] or 0)
    data['journal'].append(dict(id=id,category='encounter_completion',game_time=state.get('elapsed_minutes',0),status='gm_confirmed_completion'))
    return [dict(type='scripted',kind='encounter',text='Мастер подтвердил завершение разыгранной встречи; время окончания метели рассчитано по сохранённому броску.')]

def assess_solo(state,definition):
    people=1+sum(c.get('status')=='joined' for c in state.get('adventure',{}).get('companions',{}).values())
    verified=sum(c.combat_stats_verified for c in definition.companions if state.get('adventure',{}).get('companions',{}).get(c.id,{}).get('status')=='joined')
    return dict(people=people,mechanically_verified_members=1+verified,recommended_party=definition.recommended_party,
                warning=bool(definition.recommended_party and 1+verified<definition.recommended_party),automatic_scaling=False,lethal_risk_rule_unchanged=True)
