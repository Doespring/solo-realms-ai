import copy
import pytest
from solo.adventure_contracts import AdventureDefinition,Companion,WildernessProcedure
from solo.adventure_engine import initialize,tick,projection
from solo.adventure_expedition import forage,finish_encounter
from solo.storage import Store
from solo.contracts import Hero
from solo.rulesets import active

SOURCE={'document':'synthetic','page':1,'review':'confirmed'}

def expedition(tmp_path,**options):
    store=Store(tmp_path/'solo.sqlite3');state=store.create('Expedition',Hero(name='Лира',resources={'food':{'current':3,'maximum':10}}))['state'];preset=active(state)
    definition=AdventureDefinition(id='expedition',title='Synthetic expedition',ruleset=preset.id,ruleset_version=preset.version,reviewed=True,**options)
    initialize(state,'fixture',definition);return state,definition

def procedure(enabled=True):
    return dict(id='daily',source=SOURCE,enabled=enabled,encounter_table='encounters',period_windows=[[0,360],[360,720],[720,1080],[1080,1440]])

def table():return dict(id='encounters',formula='1d20',rows=[dict(low=1,high=20,label='Synthetic encounter')],source=SOURCE)

def test_optional_encounters_disabled_without_rng(tmp_path):
    state,_=expedition(tmp_path,tables=[table()],wilderness=[procedure(False)])
    tick(state,rng=lambda n:pytest.fail('Disabled procedure cannot roll'))
    assert not state['adventure'].get('wilderness_days')

def test_source_daily_count_seven_and_eight_mean_no_encounters(tmp_path):
    state,_=expedition(tmp_path,tables=[table()],wilderness=[procedure()])
    tick(state,rng=lambda n:6)
    day=state['adventure']['wilderness_days']['daily:0']
    assert day['periods']==[] and day['count_rolls'][0]['total']==7
    tick(state,rng=lambda n:pytest.fail('Retry must retain dice'))

def test_weather_plus_one_and_end_after_actual_encounter_completion(tmp_path):
    state,_=expedition(tmp_path,tables=[table()],wilderness=[procedure()])
    dice=iter([1,9,9,0,1]);tick(state,rng=lambda n:next(dice))
    planned=state['adventure']['wilderness_days']['daily:0']['planned'][0]
    assert planned['encounter']['total']==10 and planned['weather']['total']==11 and planned['storm_start']==480
    state['elapsed_minutes']=540;tick(state,rng=lambda n:pytest.fail('No reroll'))
    assert planned['resolved'] and projection(state)['weather']['kind']=='blizzard'
    assert 'Synthetic encounter' not in str(projection(state))
    finish_encounter(state,planned['id']);assert planned['ending']==660
    state['elapsed_minutes']=660;tick(state,rng=lambda n:pytest.fail('No reroll'))
    assert projection(state)['weather'] is None

def test_two_daily_encounters_roll_periods_separately(tmp_path):
    state,_=expedition(tmp_path,tables=[table()],wilderness=[procedure()])
    dice=iter([4,0,3,19,0,19,0]);tick(state,rng=lambda n:next(dice))
    day=state['adventure']['wilderness_days']['daily:0'];assert day['periods']==[0,3] and len(day['planned'])==2

def test_scoped_immunity_does_not_backfill_old_cold_checks(tmp_path):
    cold=dict(id='cold',kind='cold',source=SOURCE,interval_minutes=60,ability='constitution',saving_throw=True,dc=10,failure_exhaustion=1,immunity_flag='warm')
    state,_=expedition(tmp_path,survival=[cold]);state['adventure']['flags']['warm']=True;state['elapsed_minutes']=120
    tick(state,rng=lambda n:pytest.fail('Immune'));state['adventure']['flags']['warm']=False
    tick(state,rng=lambda n:pytest.fail('No historical checks'));assert state['hero']['exhaustion']==0
    state['elapsed_minutes']=180;tick(state,rng=lambda n:0);assert state['hero']['exhaustion']==1

def test_death_threshold_is_held_and_not_repeated(tmp_path):
    cold=dict(id='cold',kind='cold',source=SOURCE,ability='constitution',dc=40,failure_exhaustion=1)
    state,_=expedition(tmp_path,survival=[cold]);state['hero']['exhaustion']=5;state['elapsed_minutes']=60
    tick(state,rng=lambda n:0);assert state['adventure']['pending_hazard']['proposed']==6 and state['hero']['exhaustion']==5 and not state.get('hero_death')
    journal=copy.deepcopy(state['adventure']['journal']);tick(state,rng=lambda n:pytest.fail('Held hazard cannot repeat'))
    assert state['adventure']['journal']==journal

def test_supply_resource_and_explicit_forage_never_create_unknown_stock(tmp_path):
    food=dict(id='ration',kind='food',source=SOURCE,supply_resource='food',interval_minutes=1440)
    search=dict(id='berries',kind='forage',source=SOURCE,gain_resource='food',dc=5,add_ability_modifier=True)
    state,definition=expedition(tmp_path,survival=[food,search]);state['elapsed_minutes']=1440;tick(state,rng=lambda n:pytest.fail('Supplies need no roll'))
    assert state['hero']['resources']['food']['current']==2
    dice=iter([19,2]);forage(state,definition.survival[1],lambda n:next(dice));assert state['hero']['resources']['food']['current']==6 and state['elapsed_minutes']==1500
    with pytest.raises(ValueError):expedition(tmp_path/'invalid',survival=[{**food,'supply_resource':'invented'}])

def test_checked_time_windows_and_companion_combat_profile_are_required():
    with pytest.raises(ValueError):WildernessProcedure.model_validate({**procedure(),'period_windows':[]})
    with pytest.raises(ValueError):Companion.model_validate(dict(id='marek',npc='Марек',source=SOURCE,combat_stats_verified=True))

def test_held_hazard_blocks_registered_services_and_staged_actions_but_allows_undo(tmp_path):
    from solo.contracts import Plan,Step
    store=Store(tmp_path/'solo.sqlite3');campaign=store.create('Hazard',Hero(name='Лира'))
    # A real adventure definition is required by the shared transition hook.
    _,definition=expedition(tmp_path/'source')
    def hold(state):
        initialize(state,'fixture',definition);state['adventure']['pending_hazard']={'kind':'exhaustion'}
        return [{'type':'scripted','kind':'hazard','text':'Held'}]
    store.record_scenario(campaign['id'],'hold','Held hazard',hold)
    with pytest.raises(ValueError):store.record_scenario(campaign['id'],'bypass','Travel',lambda state:pytest.fail('Held transition executed'))
    current=store.campaign(campaign['id']);staged=store.stage(campaign['id'],'wait','Жду',Plan(summary='Wait',steps=[Step(kind='wait',text='Жду')]),current['version'],{})
    with pytest.raises(ValueError):store.apply(staged['id'])
    assert not store.undo(campaign['id'])['state'].get('adventure')
