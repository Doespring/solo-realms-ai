from solo.version import SCHEMA_VERSION
import asyncio,copy,json,sqlite3
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app,PlayRequest
from solo.contracts import Hero,Plan,Step
from solo.storage import Store
from solo.game import Game
from solo.llm import MockLLM
from solo import natural,travel
from test_core import FixedDice

@pytest.fixture
def store(tmp_path):return Store(tmp_path/'natural.sqlite3')
@pytest.fixture
def cid(store):return store.create('Новая история',Hero(name='Мирон'))['id']
def execute(store,cid,steps,rng=None,request='one'):
    p=Plan(summary='Поступок',steps=steps)
    a=store.stage(cid,request,'Поступок',p,store.campaign(cid)['version'],{'natural':True,'llm':[]})
    return store.apply(a['id'],rng)
def play(game,cid,text,key='one'):return asyncio.run(game.play(cid,PlayRequest(request_id=key,text=text)))

@pytest.mark.parametrize('text',[
 'Иду к старой башне.','Я покидаю таверну и направляюсь к старой башне.',
 'Пойду к башне по лесной тропе.','Выхожу из таверны и иду к старой башне.',
])
def test_text_travel_without_confirmation_or_interpreter(store,cid,text):
    llm=MockLLM();r=play(Game(store,llm),cid,text)
    assert r['kind']=='event' and not llm.metrics and 'plan' not in r
    state=store.campaign(cid)['state']
    assert state['location']=='Старая башня' and state['location_id']=='tower'
    assert state['elapsed_minutes']==30 and r['event']['results'][0]['meters']==2000
    assert state['hero']['hp']==12

def test_multileg_travel_and_reverse_are_real_routes(store,cid):
    game=Game(store,MockLLM())
    play(game,cid,'Иду к реке.','river');s=store.campaign(cid)['state']
    assert s['location']=='Берег реки' and s['elapsed_minutes']==50
    play(game,cid,'Возвращаюсь в таверну.','home');s=store.campaign(cid)['state']
    assert s['location'].startswith('Таверна') and s['elapsed_minutes']==100

def test_unknown_direction_asks_in_chat_no_state_change(store,cid):
    llm=MockLLM(Plan(summary='Иду на север',steps=[Step(kind='move',text='Иду на север',target='север')]))
    game=Game(store,llm);before=store.campaign(cid)
    r=play(game,cid,'Иду на север.')
    assert r['kind']=='reply' and '?' in r['reply'] and store.campaign(cid)==before
    follow=play(game,cid,'К старой башне.','answer')
    assert follow['kind']=='event' and store.campaign(cid)['state']['location']=='Старая башня'
    assert len(llm.metrics)==1 and store.open_question(cid) is None

def test_ambiguous_addressee_can_be_answered_naturally(store,cid):
    p=Plan(summary='Спрашиваю её',steps=[Step(kind='speak',text='Где её брат?',target='она')],unclear=['К кому обращаетесь?'])
    game=Game(store,MockLLM(p));before=store.campaign(cid)['state']
    r=play(game,cid,'Спрашиваю её, где брат.')
    assert r['kind']=='reply' and store.campaign(cid)['state']==before
    r=play(game,cid,'Ирине.','answer');assert r['kind']=='event'
    assert store.action(r['event']['action'])['plan']['steps'][0]['target']=='Ирина'

def test_replacement_question_does_not_leave_old_question_open(store,cid):
    llm=MockLLM(Plan(summary='Неясно',steps=[Step(kind='move',text='Иду',target='неизвестно')],unclear=['Куда?']))
    game=Game(store,llm);play(game,cid,'Хочу туда.','q1');play(game,cid,'Туда же.','q2')
    play(game,cid,'Забудь.','cancel');assert store.open_question(cid) is None

@pytest.mark.parametrize('text',[
 'Не иду к старой башне.','Я не покидаю таверну.',
])
def test_unambiguous_negation_has_no_effect(store,cid,text):
    before=store.campaign(cid);llm=MockLLM();r=play(Game(store,llm),cid,text)
    assert r['kind']=='reply' and store.campaign(cid)==before and llm.metrics==[]

def test_wait_condition_never_walks_until_confirmed(store,cid):
    step=Step(kind='move',text='Иду к башне',target='Старая башня',disposition='conditional',condition='Ирина уйдёт')
    e=execute(store,cid,[step]);assert e['results'][0]['type']=='deferred'
    assert store.campaign(cid)['state']['location'].startswith('Таверна')
    execute(store,cid,[Step(kind='wait',text='Жду')],request='wait')
    assert store.campaign(cid)['state']['location'].startswith('Таверна')
    # Источник состояния объявил отсутствие NPC, не Narrator. Новый ход возобновляет намерение.
    with store.connection(True) as db:
        s=store.campaign(cid,db)['state'];s['locations'][0]['npc'][0]['present']=False
        db.execute('UPDATE campaigns SET state=?,version=version+1 WHERE id=?',(json.dumps(s),cid))
    execute(store,cid,[Step(kind='wait',text='Жду')],request='ready')
    assert store.campaign(cid)['state']['location']=='Старая башня'
    assert store.campaign(cid)['state']['deferred']==[]

def test_negation_revokes_stored_travel_intent(store,cid):
    execute(store,cid,[Step(kind='move',text='Иду к башне',target='Старая башня',disposition='conditional',condition='Ирина уйдёт')])
    r=play(Game(store,MockLLM()),cid,'Не иду к старой башне.','revoke')
    assert r['kind']=='event' and store.campaign(cid)['state']['deferred']==[]

def test_failure_stops_sequence_without_new_rolls_or_movement(store,cid):
    dice=FixedDice(1)
    e=execute(store,cid,[Step(kind='check',text='Ищу следы'),Step(kind='move',text='Иду к башне',target='Старая башня')],dice)
    assert dice.calls==1 and e['results'][0]['success'] is False and e['results'][1]['type']=='halted'
    assert store.campaign(cid)['state']['location'].startswith('Таверна')
    assert store.apply(e['action'],dice)['id']==e['id'] and dice.calls==1

def test_success_predicate_runs_only_after_check(store,cid):
    dice=FixedDice(20)
    e=execute(store,cid,[Step(kind='check',text='Ищу следы'),Step(kind='move',text='Иду к башне',target='Старая башня',disposition='conditional',condition='Проверка успешна')],dice)
    assert dice.calls==1 and e['results'][-1]['type']=='move'
    assert store.campaign(cid)['state']['elapsed_minutes']==30

def test_obstacle_interrupts_before_later_step(store,cid):
    with store.connection(True) as db:
        s=store.campaign(cid,db)['state'];s['geography']=travel.geography(s)
        s['geography']['routes'][0].update(blocked=True,obstacle='Тропа затоплена. Как вы собираетесь пройти?')
        db.execute('UPDATE campaigns SET state=? WHERE id=?',(json.dumps(s),cid))
    e=execute(store,cid,[Step(kind='move',text='Иду к башне',target='Старая башня'),Step(kind='check',text='Ищу следы')],FixedDice())
    assert e['results'][0]['type']=='travel_stop' and e['results'][1]['type']=='halted'
    assert store.campaign(cid)['state']['elapsed_minutes']==0

def test_encounter_requires_player_decision_mid_journey(store,cid):
    with store.connection(True) as db:
        s=store.campaign(cid,db)['state'];s['geography']=travel.geography(s)
        s['geography']['routes'][0]['decision']='У башни страж просит остановиться. Что вы ему скажете?'
        db.execute('UPDATE campaigns SET state=? WHERE id=?',(json.dumps(s),cid))
    e=execute(store,cid,[Step(kind='move',text='Иду к реке',target='Берег реки')])
    s=store.campaign(cid)['state'];assert s['location']=='Старая башня' and s['elapsed_minutes']==30
    assert e['results'][0]['arrived'] is False

def test_purchase_before_travel_does_not_create_resources_or_travel(store,cid):
    llm=MockLLM(Plan(summary='Купить и уйти',steps=[Step(kind='other',text='Куплю хлеб',target='хлеб'),Step(kind='move',text='Иду к башне',target='Старая башня')]))
    before=store.campaign(cid);r=play(Game(store,llm),cid,'Сначала куплю хлеб, потом иду к башне.')
    assert r['kind']=='reply' and store.campaign(cid)==before

def test_permitted_prefix_ends_before_unsupported_operation(store,cid):
    e=execute(store,cid,[Step(kind='observe',text='Смотрю вокруг'),Step(kind='attack',text='Атакую'),Step(kind='move',text='Иду к башне',target='Старая башня')])
    assert [r['type'] for r in e['results']]==['halted','unsupported','halted']
    assert e['before_state']==e['after_state']  # Stage B: preflight before critical unsupported mechanics
    assert store.campaign(cid)['state']['hero']['hp']==12

def test_simple_speech_does_not_roll_for_word_in_question(store,cid):
    r=play(Game(store,MockLLM()),cid,'Ирина, как искать следы и убедить стража?')
    assert r['kind']=='event' and r['event']['results'][0]['type']=='speak'

def test_canonical_check_ignores_llm_dice_and_difficulty(store,cid):
    p=Plan(summary='Ищу',steps=[Step(kind='check',text='Ищу следы',ability='strength',dc=1,proficient=True,advantage=True)])
    guarded,q=natural.guard(p,'Ищу следы.',store.campaign(cid)['state']);assert not q
    e=execute(store,cid,guarded.steps,FixedDice(10))
    assert e['results'][0]['dc']==12 and e['results'][0]['ability']=='wisdom' and e['results'][0]['dice']==[10]

def test_retry_sse_regen_save_restart_undo_preserve_roll_and_time(store,cid):
    async def run():
        llm=MockLLM();game=Game(store,llm)
        request=PlayRequest(request_id='move',text='Иду к башне.')
        r=await game.play(cid,request);dup=await game.play(cid,request);assert r['event']['id']==dup['event']['id']
        [x async for x in game.narrate(r['event']['id'])];before=store.campaign(cid)
        [x async for x in game.narrate(r['event']['id'],True)];assert store.campaign(cid)==before
        cp=store.save(cid,'На тропе');assert Store(store.path).campaign(cid)==before
        store.undo(cid);assert store.campaign(cid)['state']['location'].startswith('Таверна') and 'elapsed_minutes' not in store.campaign(cid)['state']
        store.restore(cid,cp);assert store.campaign(cid)['state']==before['state']
    asyncio.run(run())


def test_old_schema4_rows_are_not_rewritten(store,cid):
    with store.connection(True) as db:
        db.execute('DROP TABLE conversations');db.execute('PRAGMA user_version=4')
        raw=tuple(db.execute('SELECT * FROM campaigns').fetchone())
    reopened=Store(store.path)
    with reopened.connection() as db:
        assert tuple(db.execute('SELECT * FROM campaigns').fetchone())==raw
        assert db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION

def test_actual_model_lost_wait_condition_does_not_move_early(store,cid):
    p=Plan(summary='Жду и иду',steps=[Step(kind='wait',text='Дожидаюсь, пока Ирина уйдёт'),Step(kind='move',text='Иду к башне',target='Старая башня')])
    game=Game(store,MockLLM(p));before=store.campaign(cid)['state']
    r=play(game,cid,'Дожидаюсь, пока Ирина уйдёт, и затем иду к башне.')
    assert r['kind']=='reply' and store.campaign(cid)['state']==before
    r=play(game,cid,'Я жду.','answer')
    assert r['kind']=='event' and store.campaign(cid)['state']['location'].startswith('Таверна')

def test_wait_duration_and_general_question_are_not_skills(store,cid):
    game=Game(store,MockLLM())
    play(game,cid,'Жду десять минут.')
    assert store.campaign(cid)['state']['elapsed_minutes']==10
    r=play(game,cid,'Что за следы?','look')
    assert r['event']['results'][0]['type']=='observe'

def test_requesting_permission_is_not_a_critical_threat_error():
    from semantic_score import score
    from cases_pass16 import INDEPENDENT
    plan=Plan(summary='Прошу разрешение',steps=[Step(kind='speak',text='Прошу капитана разрешить разговор с пленником',target='капитан')])
    assert score(INDEPENDENT[11],plan.model_dump())['critical']==[]

def test_real_model_hallucinated_tavern_for_north_is_blocked(store,cid):
    execute(store,cid,[Step(kind='move',text='Иду к башне',target='Старая башня')])
    p=Plan(summary='Иду',steps=[Step(kind='move',text='Иду в таверну',target='Таверна «Последний огонь»')])
    before=store.campaign(cid)['state']
    r=play(Game(store,MockLLM(p)),cid,'Иду на север.','north')
    assert r['kind']=='reply' and store.campaign(cid)['state']==before

def test_incidental_river_is_not_destination(store,cid):
    text='Иду в неизвестный город мимо реки.'
    assert natural.quick(text,store.campaign(cid)['state']) is None
    p=Plan(summary='Иду к реке',steps=[Step(kind='move',text='Иду к реке',target='Берег реки')])
    r=play(Game(store,MockLLM(p)),cid,text)
    assert r['kind']=='reply' and store.campaign(cid)['state']['location'].startswith('Таверна')

def test_temporal_dependency_overrides_order_of_mention(store,cid):
    p=Plan(summary='Ищу, затем иду',steps=[Step(kind='move',text='Иду к башне',target='Старая башня',disposition='conditional',condition='Проверка успешна'),Step(kind='check',text='Ищу следы')])
    checked,q=natural.guard(p,'Если проверка успешна, иду к башне. Сначала ищу следы.',store.campaign(cid)['state'])
    assert not q and checked.steps[0].kind=='check'
    e=execute(store,cid,checked.steps,FixedDice(20));assert e['results'][-1]['type']=='move'

def test_explicit_wait_with_future_move_needs_no_confirmation(store,cid):
    p=Plan(summary='Жду и потом иду',steps=[Step(kind='move',text='Иду к башне',target='Старая башня',disposition='conditional',condition='Ирина уйдёт'),Step(kind='wait',text='Пока остаюсь здесь')])
    checked,q=natural.guard(p,'Если Ирина уйдёт, отправлюсь к башне. Пока остаюсь здесь.',store.campaign(cid)['state'])
    assert not q and checked.steps[0].kind=='wait'
    e=execute(store,cid,checked.steps);assert e['results'][-1]['type']=='deferred'

def test_save_undo_correction_and_proposal_acceptance_have_text_equivalents(store,cid):
    game=Game(store,MockLLM());r=play(game,cid,'Иду к башне.')
    play(game,cid,'Мастер, сохрани игру.','save');assert store.saves(cid)
    play(game,cid,'Мастер, исправь описание: Тропа вывела к башне.','edit')
    assert store.event(r['event']['id'])['narrative']=='Тропа вывела к башне.'
    store.narrative(r['event']['id'],'Тропа у башни','complete',structure={'description':'Тропа у башни','dialogue':[],
        'proposals':[{'category':'discovery','text':'На камне изображён знак гильдии.'}]})
    play(game,cid,'Мастер, прими предложенное сведение.','accept')
    assert store.campaign(cid)['state']['facts']==['На камне изображён знак гильдии.']
    play(game,cid,'Мастер, отмени последний ход.','undo')
    assert store.campaign(cid)['state']['facts']==[] and game.llm.metrics==[]

def test_literal_json_braces_inside_npc_text_do_not_retract_stream():
    from solo.narration import StreamPresentation
    data={'description':'Свет фонаря.','dialogue':[{'speaker':'Лада','text':'Это символы: }], "text": { — внутри речи.'}],'proposals':[]}
    raw=json.dumps(data,ensure_ascii=False);presentation=StreamPresentation(['Лада']);parts=[]
    for letter in raw:parts.append(presentation.feed(letter))
    assert ''.join(parts)=='Свет фонаря.\n\nЛада: «'+data['dialogue'][0]['text']+'»'
    assert presentation.finish().model_dump()==data

@pytest.mark.parametrize('text',['Привет.','Где твой брат?','Расскажи о письме.'])
def test_ordinary_speech_to_sole_npc_needs_no_interpreter(store,cid,text):
    llm=MockLLM();r=play(Game(store,llm),cid,text)
    assert r['kind']=='event' and llm.metrics==[] and r['event']['results'][0]['target']=='Ирина'
