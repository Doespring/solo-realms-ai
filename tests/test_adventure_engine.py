import copy,json
import pytest
from solo.adventure_contracts import AdventureDefinition
from solo.adventure_engine import initialize,tick,projection,roll,after_transition
from solo.storage import Store
from solo.contracts import Hero
from solo.combat_view import public_campaign
from solo.game import SceneCompiler
from solo.rulesets import active

SOURCE={'document':'synthetic','page':1,'review':'confirmed'}

def setup(tmp_path,events=None,tables=None,trackers=None):
    store=Store(tmp_path/'solo.sqlite3');campaign=store.create('Synthetic adventure',Hero(name='Лира'));state=campaign['state'];preset=active(state).model_dump()
    definition=AdventureDefinition(id='module',title='Reviewed synthetic module',ruleset=preset['id'],ruleset_version=preset['version'],reviewed=True,events=events or [],tables=tables or [],trackers=trackers or [])
    initialize(state,'synthetic-bundle',definition)
    return store,campaign,state,definition

def event(id='gate',**kwargs):
    return dict(id=id,category='conditional',conditions=[{'path':'adventure.flags.door_open','value':True}],source=SOURCE,**kwargs)

def test_conditional_event_waits_for_actual_state_and_resolves_once(tmp_path):
    store,campaign,state,definition=setup(tmp_path,[event(effects=[{'kind':'tracker','target':'alarm','value':1}])],trackers=[dict(id='alarm',name='Alarm',source=SOURCE)])
    tick(state);assert state['adventure']['trackers']['alarm']==0
    state['adventure']['flags']['door_open']=True;tick(state);tick(state)
    assert state['adventure']['trackers']['alarm']==1 and len(state['adventure']['journal'])==1

def test_unknown_is_not_false_and_negation_cannot_create_canonical_claim(tmp_path):
    body=dict(id='claim',category='conditional',conditions=[{'path':'adventure.flags.invisible','op':'ne','value':True}],effects=[{'kind':'flag','target':'triggered','value':True}],source=SOURCE)
    _,_,state,_=setup(tmp_path,[body]);tick(state);assert 'triggered' not in state['adventure']['flags']
    state['adventure']['flags']['invisible']=False;tick(state);assert state['adventure']['flags']['triggered'] is True

def test_recommendations_and_optional_scenes_do_not_force_random_encounters(tmp_path):
    body=dict(id='pacing',category='optional',instruction='recommendation',effects=[{'kind':'table','target':'encounters'}],source=SOURCE)
    table=dict(id='encounters',formula='1d20',rows=[{'low':1,'high':20,'label':'Example'}],source=SOURCE)
    _,_,state,_=setup(tmp_path,[body],[table]);tick(state,rng=lambda n:pytest.fail('Recommendation must not roll dice'))
    assert not state['adventure']['journal']

def test_real_roll_table_and_followup_check_have_provenance_and_are_reproducible(tmp_path):
    body=event(effects=[{'kind':'table','target':'encounters'},{'kind':'check','target':'safe','ability':'wisdom','dc':12}])
    table=dict(id='encounters',formula='2d6+3',rows=[{'low':5,'high':15,'label':'Encounter','effects':[{'kind':'flag','target':'chosen','value':'table_row'}]}],source=SOURCE)
    _,_,state,_=setup(tmp_path,[body],[table]);state['adventure']['flags']['door_open']=True;values=iter([1,5,0]);tick(state,rng=lambda n:next(values))
    records=state['adventure']['journal'][0]['rolls'];assert records[0]['dice']==[2,6] and records[0]['total']==11
    assert records[0]['source']['page']==1 and records[1]['check']['success'] is False
    assert state['adventure']['flags']['chosen']=='table_row'

def test_time_and_repeating_events_use_game_clock_not_windows(tmp_path):
    body=dict(id='supply',category='repeating',repeat_minutes=10,effects=[{'kind':'tracker','target':'days','value':1}],source=SOURCE)
    _,_,state,_=setup(tmp_path,[body],trackers=[dict(id='days',name='Supplies',source=SOURCE)])
    state['elapsed_minutes']=40;tick(state);assert state['adventure']['trackers']['days']==5
    tick(state);assert state['adventure']['trackers']['days']==5 and state['adventure']['schedule']['supply']==50

def test_dependencies_can_enable_followup_in_same_transaction(tmp_path):
    first=event(id='a',effects=[{'kind':'flag','target':'found','value':True}]);second=dict(id='b',category='mandatory',prerequisites=['a'],source=SOURCE,effects=[{'kind':'evidence','target':'clue','text':'A visible clue','public':True}])
    _,_,state,_=setup(tmp_path,[second,first]);state['adventure']['flags']['door_open']=True;tick(state)
    assert state['adventure']['events']['b']=='resolved' and projection(state)['evidence'][0]['text']=='A visible clue'

def test_gm_handout_cannot_be_revealed_even_by_a_reviewed_event(tmp_path):
    body=event(effects=[{'kind':'handout','target':'hidden'}]);store,campaign,state,definition=setup(tmp_path)
    definition=definition.model_copy(update={'media':['hidden'],'events':[]})
    raw=definition.model_dump();raw['events']=[body];initialize(state,'fixture',AdventureDefinition.model_validate(raw));state['adventure']['media_records']['hidden']={'scope':'gm','reviewed':True,'sanitized':False};state['adventure']['flags']['door_open']=True
    with pytest.raises(ValueError):tick(state)
    assert not state['adventure']['known_media']

def test_projection_excludes_private_journal_rolls_flags_and_future_media(tmp_path):
    _,campaign,state,_=setup(tmp_path,[event(effects=[{'kind':'flag','target':'SECRET_PASSWORD','value':'UNSEEN'}])]);state['adventure']['flags']['door_open']=True;tick(state)
    public=json.dumps(public_campaign({**campaign,'state':state}),ensure_ascii=False)
    assert 'UNSEEN' not in public and 'SECRET_PASSWORD' not in public and 'source' not in json.dumps(projection(state))

def test_event_retry_undo_and_restore_do_not_reroll_or_lose_scenario_state(tmp_path):
    body=dict(id='clock',category='timed',after_minutes=10,effects=[{'kind':'tracker','target':'alarm','value':1}],source=SOURCE)
    store,campaign,state,definition=setup(tmp_path,[body],trackers=[dict(id='alarm',name='Alarm',source=SOURCE)])
    store.record_scenario(campaign['id'],'attach','Attach',lambda target:(target.update(adventure=copy.deepcopy(state['adventure'])) or [{'type':'scripted','kind':'attach','text':'Attached'}]))
    save=store.save(campaign['id'],'Before clock')
    def advance(target):target['elapsed_minutes']=10;return [{'type':'scripted','kind':'wait','text':'Waited'}]
    result=store.record_scenario(campaign['id'],'clock','Wait',advance);repeat=store.record_scenario(campaign['id'],'clock','Wait',advance)
    assert result['id']==repeat['id'] and store.campaign(campaign['id'])['state']['adventure']['trackers']['alarm']==1
    store.undo(campaign['id']);assert store.campaign(campaign['id'])['state']['adventure']['trackers']['alarm']==0
    store.restore(campaign['id'],save);assert store.campaign(campaign['id'])['state']['adventure']['events']['clock']=='pending'

def test_strict_unreviewed_unsupported_or_wrong_edition_cannot_start(tmp_path):
    _,_,state,definition=setup(tmp_path)
    for changes in [{'reviewed':False},{'unsupported':['missing spell']},{'ruleset':'different-edition'}]:
        with pytest.raises(ValueError):initialize(state,'bad',definition.model_copy(update=changes))

def test_randomness_cannot_be_supplied_as_model_prose():
    with pytest.raises(ValueError):roll('1d20',lambda n:'20')
    assert roll('1d100-3',lambda n:99)['total']==97

def test_round_clock_advances_six_seconds_only_after_completed_round(tmp_path):
    _,_,state,_=setup(tmp_path,[dict(id='sixseconds',category='timed',after_minutes=.1,effects=[{'kind':'flag','target':'clock','value':True}],source=SOURCE)])
    state['combat']={'active':True,'round':1};before=copy.deepcopy(state);results=[{'type':'scripted','kind':'combat','text':'Confirmed'}]
    after_transition(state,results,before=before);assert state.get('elapsed_minutes',0)==0
    state['combat']['round']=2;after_transition(state,results,before=before)
    assert state['elapsed_minutes']==.1 and state['adventure']['flags']['clock'] is True

def test_secret_weather_note_is_not_a_player_or_narrator_field(tmp_path):
    _,_,state,_=setup(tmp_path,[dict(id='weather',category='timed',effects=[{'kind':'weather','target':'blizzard','text':'SECRET_GM_NOTE'}],source=SOURCE)])
    tick(state);assert projection(state)['weather'] is None and 'SECRET_GM_NOTE' not in json.dumps(public_campaign({'state':state}))
