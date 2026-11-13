"""Естественный ввод и консервативная проверка смысла перед общей механикой.

Быстрые пути ограничены ясными низкорисковыми конструкциями; остальные реплики
разбирает Interpreter. Guard не является доказательством полного понимания языка.
"""
import copy
import re
from .contracts import Plan,Step,Hero
from . import rules,travel

def norm(text):return travel.norm(text)
FUTURE=re.compile(r'\b(?:завтра|позже|вечером|утром|планир\w*|собираюсь)\b')
CONDITIONAL=re.compile(r'\b(?:если|когда|после\s+того|пока|лишь\s+после|только\s+после)\b')
MOVE=re.compile(r'\b(?:иду|идем|пойду|пойдем|шагаю|топаю|продолжаю\s+путь|отправляюсь|отправлюсь|направляюсь|направлюсь|перехожу|покидаю|покину|выхожу|выйду|возвращаюсь|вернусь|двигаюсь|отступаю|отступлю)\b')
# Второе действие в той же реплике («иду к реке и атакую»): быстрый путь движения
# обязан уступить Interpreter, иначе второе действие пропадает молча. Оборот
# «иду к реке, чтобы атаковать» — это цель пути, а не второе действие.
SECOND_ACTION=re.compile(r'\b(?:атак\w*|стреля\w*|колду\w*|бью|бросаю|беру|открываю|закрываю|обыскива\w*|осматрива\w*|ищу|слуша\w*)\b')
PURPOSE=re.compile(r'\b(?:чтобы|чтоб|для\s+того\s+чтобы)\b')
NEGATION=re.compile(r'\b(?:не|нет|никогда|не\s+стану|не\s+собираюсь)\b')

def outside_quotes(text):
    return re.sub(r'«[^»]*»|"[^"\n]*"',' ',text)

def named_destinations(text,state):
    """Цель должна присутствовать в направлении игрока, не в догадке LLM.

    «в город мимо реки» не означает «к реке»; «из таверны» — источник, не цель.
    """
    result=set();s=norm(outside_quotes(text))
    chunks=re.split(r'[.!?;]|\b(?:затем|потом)\b',s)
    for chunk in chunks:
        found=list(MOVE.finditer(chunk))
        if found:tail=chunk[found[-1].end():].strip()
        elif re.match(r'^\s*(?:к|в|на)\s+',chunk):tail=chunk.strip()
        else:continue
        tail=re.split(r'\b(?:мимо|стараясь|чтобы|потому|если|по\s+(?:дороге|тропе)|через)\b',tail)[0]
        if tail.startswith('из '):
            after=re.search(r'\s(?:к|в|на)\s+',tail)
            if not after:continue
            tail=tail[after.start():].strip()
        dest=travel.destination(tail,state)
        if dest:
            if found and found[-1][0] in ('покидаю','покину','выхожу','выйду') and dest==state['location']:continue
            result.add(dest)
    return result

def npc_target(text,state):
    matches=[];s=norm(text)
    for npc in next(p for p in state['locations'] if p['name']==state['location'])['npc']:
        name=npc['name'];stem=norm(name)
        if stem.endswith(('а','я')):stem=stem[:-1]
        if re.search(r'\b'+re.escape(stem)+r'\w*\b',s):matches.append(name)
    return matches[0] if len(matches)==1 else None

def quick(text,state):
    from .interactions import quick as object_quick
    interaction=object_quick(text,state)
    if interaction:return interaction
    s=norm(text).strip('.!? ');outside=norm(outside_quotes(text))
    target=npc_target(text,state)
    present=[n for n in next(p for p in state['locations'] if p['name']==state['location'])['npc'] if n.get('present',True)]
    if len(present)==1 and not MOVE.search(outside) and not re.search(r'\b(?:затем|потом|сначала|атакую|открываю|беру|покупаю|использую|колдую)\b',outside):
        if re.match(r'^(?:привет|здравствуй\w*|добрый\s+(?:день|вечер)|спасибо|благодарю|скажи|расскажи|где\b|почему\b|зачем\b|когда\b)\b',s):
            return Plan(summary='Разговор',steps=[Step(kind='speak',text=text,target=present[0]['name'])])
    if re.match(r'^(?:я\s+)?не\s+(?:иду|пойду|выхожу|покидаю|направляюсь|отправляюсь|собираюсь\s+(?:идти|пойти|выходить|отправляться))\b',s) and not (SECOND_ACTION.search(s) and not PURPOSE.search(s)):
        dest=travel.destination(s,state)
        if dest:return Plan(summary='Отказ от путешествия',steps=[Step(kind='move',text=text,target=dest,disposition='negated')])
    # Обращение/вопрос к одному находящемуся здесь NPC — речь, не изменение мира.
    if target and (re.match(r'^'+re.escape(norm(target).rstrip('ая'))+r'\w*\s*[,!?:]',s) or re.match(r'^(?:спрашиваю|говорю|обращаюсь|рассказываю|прошу)\b',s)):
        if not MOVE.search(outside) and not re.search(r'\b(?:затем|потом|сначала|атакую|открываю|беру|покупаю|использую|колдую)\b',outside):
            return Plan(summary='Разговор',steps=[Step(kind='speak',text=text,target=target)])
    if s in ('жду','я жду','подожду','остаюсь на месте'):
        return Plan(summary='Ожидание',steps=[Step(kind='wait',text=text)])
    if re.match(r'^(?:я\s+)?(?:только\s+)?(?:жду|подожду|дожидаюсь)\b',s) and not re.search(r'\b(?:затем|потом|откры\w*|покуп\w*|беру|говорю)\b',s) and not MOVE.search(s):
        return Plan(summary='Ожидание',steps=[Step(kind='wait',text=text)])
    if re.match(r'^(?:что\s+(?:происходит|вокруг|видно)|как\s+выглядит|что\s+за)\b',s):
        return Plan(summary='Осмотр сцены',steps=[Step(kind='observe',text=text,target=target or '')])
    if re.fullmatch(r'(?:я\s+)?(?:осматриваюсь|оглядываюсь|осматриваю\s+(?:комнату|таверну|место|окрестности)|смотрю\s+вокруг)',s):
        return Plan(summary='Осмотр',steps=[Step(kind='observe',text=text)])
    if MOVE.search(s) and not CONDITIONAL.search(s) and not FUTURE.search(s) and not NEGATION.search(s) and not (SECOND_ACTION.search(s) and not PURPOSE.search(s)):
        # Нет цитат, косвенной речи или предварительной покупки/другого действия.
        if re.match(r'^(?:я\s+)?(?:'+MOVE.pattern[2:-2]+')',s) and not re.search(r'\b(?:говор\w*|сказ\w*|предлаг\w*|куп\w*|сначала|собира|хочу|скрыт\w*|незамет\w*|стараясь|патрул\w*)\b',s) and s==norm(outside_quotes(text)).strip('.!? '):
            targets=named_destinations(s,state);target=next(iter(targets)) if len(targets)==1 else None
            # «выхожу из таверны и иду к башне»: конечная цель берётся из последнего
            # направляющего глагола; исходное место не считается вторым назначением.
            if target:return Plan(summary='Путешествие',steps=[Step(kind='move',text=text,target=target)])
    return None

def guard(plan,text,state):
    plan=plan.model_copy(deep=True);s=norm(outside_quotes(text));present=next(p for p in state['locations'] if p['name']==state['location'])
    if plan.unclear:return None,'Уточните, пожалуйста: '+plan.unclear[0]
    if plan.branches and not any(x.condition for x in plan.steps):return None,'Какой из этих путей вы выбираете сейчас?'
    for step in plan.steps:
        step.ability=step.dc=None;step.skill='';step.proficient=step.advantage=step.disadvantage=False
        if step.kind=='move':
            resolved=travel.destination(step.target or step.text,state)
            if resolved:step.target=resolved
            if step.disposition!='negated' and step.target not in named_destinations(text,state):
                return None,'Куда именно вы хотите идти? Для этого направления пока не известна дорога.'
        if step.disposition!='now':continue
        if re.match(r'^(?:я\s+)?не\b',norm(step.text)) and norm(step.text) in norm(text):
            step.disposition='negated';continue
        explicit_wait=step.kind=='wait' and re.search(r'\b(?:пока|сейчас|до\s+того)\s+(?:я\s+)?(?:жду|остаюсь)',s)
        current_clauses=re.findall(r'\b(?:сначала|сейчас)\s+([^.!?;]+)',s)
        explicit_current=any(norm(step.text).strip('.!? ') in c for c in current_clauses)
        if step.kind in ('other','use','attack') and not explicit_current and CONDITIONAL.search(s):
            return None,'Какой шаг вы выполняете сейчас, до наступления условия?'
        if step.kind!='speak' and not explicit_wait and not explicit_current and re.match(r'^(?:если|когда|завтра|утром|позже|после\s+того)\b',s):
            return None,'Вы ждёте наступления условия или хотите сделать что-то уже сейчас?'
        if step.kind=='move':
            if not MOVE.search(s) or re.search(r'\b(?:говор\w*|сказ\w*|предлаг\w*|притвор\w*|делаю\s+вид)\b',s):
                return None,'Вы хотите действительно отправиться туда сейчас?'
            if NEGATION.search(s) or FUTURE.search(s) or CONDITIONAL.search(s):
                return None,'Какой шаг вы выполняете сейчас, до наступления условия?'
            target=travel.destination(step.target or step.text,state)
            if not target:return None,'Куда именно вы хотите идти? Это направление ещё не связано с известной дорогой.'
            try:travel.itinerary(state,target)
            except ValueError as e:return None,str(e)
            step.target=target
        if step.kind=='speak':
            target=npc_target(step.target or step.text,state)
            if not target:
                if not step.target and len(present['npc'])==1 and not re.search(r'\b(?:ему|ей|им|он|она)\b',norm(text)):
                    target=present['npc'][0]['name']
                else:return None,'К кому вы обращаетесь?'
            step.target=target
        if step.kind in ('observe','check') and re.search(r'\b(?:ту|тот|этому|которой\s+он|которую\s+он)\b',s) and not step.target:
            return None,'Что именно вы хотите осмотреть?'
    # Временная зависимость важнее порядка упоминания: «если проверка успешна,
    # иду; сначала ищу следы» означает сначала проверку.
    ordered=[];remaining=list(plan.steps)
    for step in list(remaining):
        if step.disposition=='conditional' and ('успеш' in norm(step.condition) or 'успех' in norm(step.condition)) and ('провер' in norm(step.condition) or 'осмотр' in norm(step.condition)):
            checks=[] if any(x.after for x in plan.steps) else [x for x in remaining if x.disposition=='now' and (x.kind=='check' or check_for(x,state)[0] is not None)]
            for x in checks:ordered.append(x);remaining.remove(x)
    if re.search(r'\b(?:сейчас|пока|до\s+того)\s+(?:я\s+)?(?:жду|остаюсь)',s):
        for step in list(remaining):
            if step.kind=='wait' and step.disposition=='now':ordered.append(step);remaining.remove(step)
    plan.steps=ordered+remaining
    return plan,''

def predicate(condition,state,last_results):
    s=norm(condition)
    observable=re.fullmatch(r'(?:если|когда)\s+(.+?)\s+(не\s+)?(открыт[ао]?|закрыт[ао]?)',s)
    if observable:
        from .interactions import locate
        _,found=locate(state,observable[1])
        if len(found)==1 and found[0].kind=='door' and not found[0].taken:
            truth=found[0].opened if observable[3].startswith('открыт') else not found[0].opened
            return not truth if observable[2] else truth
    if ('успеш' in s or 'успех' in s) and ('провер' in s or 'осмотр' in s):
        checks=[r for r in last_results if r['type']=='check']
        return checks[-1]['success'] if checks else None
    # Только подтверждённые факты, не художественный журнал/догадки модели.
    if any(norm(f)==s for f in state.get('facts',[])):return True
    for place in state['locations']:
        if place['name']!=state['location']:continue
        for npc in place.get('npc',[]):
            name=norm(npc['name'])
            if name in s and re.search(r'уйд\w*|уш\w*|покин\w*|отсутств\w*',s):
                if npc.get('present') is False:return True
                return False
    return None

def inferred_skill(text):
    for stems,skill in [('убежда|убед|уговор|уговар','persuasion'),('обман|лгу','deception'),('скрыт|незамет|подкрад','stealth'),('след','survival'),('слуш|ловуш|надпис|печать','perception')]:
        if re.search(stems,norm(text)):return skill
    return ''

def check_for(step,state):
    place=next(p for p in state['locations'] if p['name']==state['location'])
    # Сценарий назначает точные проверки; LLM не выбирает произвольные числа.
    catalogs=place.get('checks',{})
    text=norm(step.text)
    if step.kind=='speak' and not re.match(r'^(?:я\s+)?(?:убеждаю|уговариваю|обманываю|лгу|пытаюсь\s+убедить)\b',text):return None,None
    if step.kind=='observe' and not re.match(r'^(?:я\s+)?(?:ищу|пытаюсь|проверяю|прислушиваюсь|разбираю|исследую|осматриваю)\b',text):return None,None
    for key,value in catalogs.items():
        if any(norm(word) in text for word in value['triggers']):
            from .rulesets import SKILLS
            skill=value.get('skill',inferred_skill(text))
            if skill and SKILLS.get(skill)!=value['ability']:
                if value.get('skill'):raise ValueError('Навык сценария не соответствует характеристике')
                skill=''
            return Step(kind='check',text=step.text,target=step.target,ability=value['ability'],dc=value['dc'],
                        skill=skill,proficient=value.get('proficient',False),advantage=value.get('advantage',False),disadvantage=value.get('disadvantage',False)),key
    # Базовые сценарные проверки трёх стартовых мест. Это настройки приключения,
    # а не изменение формулы SRD. Старые сохранения получают их без перезаписи.
    if place['name'] in [n['name'] for n in travel.NODES]:
        for words,ability in [(['след','надпис','печать','ловуш','слуш','прислуш'],'wisdom'),(['скрыт','незамет','подкрад'],'dexterity'),(['обман','убежда','убед','уговор','уговар','лгу'],'charisma')]:
            if any(w in text for w in words):
                return Step(kind='check',text=step.text,target=step.target,ability=ability,skill=inferred_skill(text),dc=12), 'start:'+ability
    return None,None

def resolve(state,plan,rng=None,*,scenario_checks=True,resume=True):
    """Последовательность с остановкой на провале/препятствии/новом решении."""
    from .first_playable import guard_plan,stage_of
    guard_plan(state,plan)
    # A verified distant view is observable prose, not an interactable scene
    # object. Do not turn "осматриваю дорогу" into an unregistered door/item.
    if stage_of(state):scenario_checks=False;resume=False
    original_state=copy.deepcopy(state)
    results=[];stopped=False;deferred=[];ready=[];waiting=[]
    cancelled=[s for s in plan.steps if s.disposition=='negated']
    if not scenario_checks:
        rules.validate_plan(plan)
        for step in plan.steps:
            if step.kind=='move' and step.disposition=='now':travel.itinerary(state,step.target)
    for old in state.get('deferred',[]):
        step=Step.model_validate({k:v for k,v in old.items() if k in Step.model_fields})
        if any(s.kind==step.kind and s.target and norm(s.target)==norm(step.target) for s in cancelled):continue
        if resume and not step.after and len(ready)+len(plan.steps)<8 and step.disposition=='conditional' and predicate(step.condition,state,[]) is True and step.kind in ('speak','observe','check','move','wait'):
            if step.kind=='move':
                target=travel.destination(step.target or step.text,state)
                if not target:waiting.append(old);continue
                step.target=target
                try:travel.itinerary(state,target)
                except ValueError:waiting.append(old);continue
            step.action_id='';ready.append(step)
        else:waiting.append(old)
    state['deferred']=waiting
    # Возвращаем также фактически использованный план: SceneCompiler сопоставляет
    # результаты с возобновлёнными шагами, а не с прежним индексом реплики.
    used=Plan(summary=plan.summary,steps=ready+plan.steps,unclear=[],branches=plan.branches)
    from .interactions import classify,perform,result as interaction_result
    # Critical missing mechanics are discovered before a single die or mutation.
    def unsupported(step):
        if step.kind not in ('attack','use','other'):return None
        bound=classify(step,state)
        if isinstance(bound,tuple) or isinstance(bound,dict) and bound['status']!='critical_unsupported':return None
        optional=bool(re.match(r'^(?:необязательно|по возможности)\b',norm(step.text)))
        return interaction_result('optional_unsupported' if optional else 'critical_unsupported','Необязательная механика не исполнена.' if optional else 'Критическая механика действия не поддерживается. Последовательность не исполнена; эффект не придуман.')
    def potentially_current(step):return step.disposition=='now' or step.disposition=='conditional' and predicate(step.condition,state,[]) is not False
    critical=any(potentially_current(s) and (r:=unsupported(s)) and r['status']=='critical_unsupported' for s in used.steps) if scenario_checks else False
    completed=set()
    for step in used.steps:
        independent=bool(re.match(r'^(?:независимо от этого|в любом случае)\b',norm(step.text))) and not step.after
        if stopped and independent and not critical:stopped=False
        if step.after and any(parent not in completed for parent in step.after):stopped=True
        missing=unsupported(step) if scenario_checks and potentially_current(step) else None
        if critical and step.disposition!='negated':
            results.append(missing if missing and missing['status']=='critical_unsupported' else {'type':'halted','status':'blocked_dependency','text':step.text,'note':'Последовательность содержит критически неподдерживаемое действие.'});continue
        if stopped:
            results.append({'type':'halted','text':step.text,'note':'Этот шаг ещё не выполнялся.'});continue
        if step.disposition=='negated':
            results.append({'type':'denied','text':step.text});continue
        if step.disposition=='intended':
            deferred.append(step.model_dump());results.append({'type':'deferred','text':step.text,'disposition':'intended','condition':step.condition});stopped=True;continue
        if step.disposition=='conditional':
            truth=predicate(step.condition,state,results) if resume else None
            if truth is not True:
                deferred.append(step.model_dump());results.append({'type':'deferred','text':step.text,'disposition':'conditional','condition':step.condition});stopped=True;continue
        if missing:
            if missing['status']=='critical_unsupported':
                critical=True
                results=[r if r['type'] in ('denied','halted','deferred','unsupported') else {'type':'halted','status':'rolled_back','text':prior.text,'note':'Переход отменён целиком: зависимая механика не поддерживается.'} for prior,r in zip(used.steps,results)]
            results.append(missing);stopped=True;continue
        if scenario_checks:
            interaction=perform(state,step,rng)
            if interaction is not None:
                results.append(interaction);stopped=interaction['status']!='supported'
                if not stopped and step.action_id:completed.add(step.action_id)
                continue
        if step.action_id:completed.add(step.action_id)
        if step.kind in ('attack','use','other'):
            results.append({'type':'unsupported','status':'critical_unsupported','text':step.text,'reply':'Эта попытка пока не может быть рассчитана: требуется зарегистрированная команда боя, предмета или сделки; полный каталог заклинаний не поддерживается. Ничего не израсходовано.'});stopped=True;continue
        if step.kind=='move':
            try:result=travel.advance(state,step.target)
            except ValueError as exc:result={'type':'travel_stop','text':str(exc),'target':state['location'],'arrived':False,'minutes':0,'meters':0,'route':[]}
            results.append(result);stopped=result['type']=='travel_stop';continue
        if step.kind=='speak' and scenario_checks:
            npc=next(p for p in state['locations'] if p['name']==state['location'])['npc']
            if step.target not in [n['name'] for n in npc if n.get('present',True)]:
                results.append({'type':'needs_context','text':step.text,'reply':'Собеседника здесь сейчас нет. С кем вы хотите говорить?'});stopped=True;continue
        if step.kind=='wait':
            match=re.search(r'\b(\d{1,3}|одну|один|две|два|пять|десять|пятнадцать|двадцать|тридцать)\s+(минут\w*|час\w*)',norm(step.text))
            if match:
                words={'одну':1,'один':1,'две':2,'два':2,'пять':5,'десять':10,'пятнадцать':15,'двадцать':20,'тридцать':30}
                minutes=(int(match[1]) if match[1].isdigit() else words[match[1]])*(60 if match[2].startswith('час') else 1)
                state['elapsed_minutes']=state.get('elapsed_minutes',0)+minutes
                results.append({'type':'wait','text':step.text,'minutes':minutes});continue
        check_step,key=check_for(step,state) if scenario_checks else (step if step.kind=='check' else None,None)
        if step.kind=='check' and check_step is None:
            results.append({'type':'needs_context','text':step.text,'reply':'Что именно вы хотите получить этой попыткой? Здесь пока не задано подходящее испытание.'});stopped=True;continue
        if check_step is not None:
            from .rulesets import active
            result=rules.check(Hero.model_validate(state['hero']),check_step,rng,preset=active(state));result['text']=step.text;result['scenario_check']=key
            result['ruling']='Проверка задана сценарием; бросок выполняет Rules Engine'
            results.append(result);stopped=not result['success'];continue
        results.append({'type':step.kind,'text':step.text,'target':step.target})
    if critical:
        state.clear();state.update(original_state);return results,used
    state['deferred']=(state.get('deferred',[])+deferred)[-20:]
    from .world import tick
    messages,evidence=tick(state)
    if results and (messages or evidence):
        results[-1].setdefault('world_events',[]).extend(messages);results[-1].setdefault('memory_evidence',[]).extend(evidence)
    state['turn']+=1
    return results,used
