import copy,json,random
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Hero,Step,Plan
from solo.rulesets import BUILTINS,Ruleset,RulesetEngine,RulesetRepository,active
from solo.rules_api import apply_rest
from solo.storage import Store

class Dice:
    def __init__(self,*values):self.values=iter(values);self.calls=0
    def randint(self,a,b):
        self.calls+=1;value=next(self.values);assert a<=value<=b;return value

def test_edition_exhaustion_and_surprise_have_real_effects():
    hero=Hero(name='Герой',exhaustion=2)
    old,new=[RulesetEngine(BUILTINS[id]) for id in ['dnd2014','dnd2024']]
    step=Step(kind='check',text='Осмотр',ability='wisdom',dc=12)
    a=old.check(hero,step,Dice(15,3));b=new.check(hero,step,Dice(15))
    assert a['dice']==[15,3] and a['total']==4 and not a['success']
    assert b['dice']==[15] and b['total']==12 and b['success']
    assert old.sheet(hero)['speed_ft']==15 and new.sheet(hero)['speed_ft']==20
    healthy=Hero(name='Герой')
    assert old.initiative(healthy,surprised=True,rng=Dice(15))['skip_first_turn']
    surprise=new.initiative(healthy,surprised=True,rng=Dice(15,3))
    assert not surprise['skip_first_turn'] and surprise['selected']==3

def test_expertise_advantage_cancellation_and_no_automatic_check_crit():
    engine=RulesetEngine(BUILTINS['dnd2024']);hero=Hero(name='Герой',skill_proficiencies=['stealth'],expertise=['stealth'])
    result=engine.skill(hero,'stealth',15,rng=Dice(10),advantage=True,disadvantage=True)
    assert result['dice']==[10] and result['total']==16 and result['success']
    assert not engine.skill(hero,'athletics',30,rng=Dice(20))['success']
    assert engine.skill(hero,'athletics',2,rng=Dice(1))['success']

def test_rest_edition_differences_and_idempotence(tmp_path):
    for id,expected in [('dnd2014',2),('dnd2024',4)]:
        store=Store(tmp_path/(id+'.sqlite3'));cid=store.create('Отдых',Hero(name='Герой',level=4,hp=2,max_hp=20,hit_dice_available=0,exhaustion=1),BUILTINS[id])['id']
        before=store.campaign(cid)['state'];event=apply_rest(store,cid,'rest','long',food_water=True)
        after=store.campaign(cid)['state']
        assert after['hero']['hit_dice_available']==expected and after['hero']['hp']==20 and after['hero']['exhaustion']==0
        assert apply_rest(store,cid,'rest','long',food_water=True)['id']==event['id']
        with pytest.raises(ValueError):apply_rest(store,cid,'rest','short')
        with pytest.raises(ValueError):apply_rest(store,cid,'new-rest','long',food_water=True)
        store.undo(cid);assert store.campaign(cid)['state']==before

def test_2024_short_rest_minimum_healing_differs_from_2014(tmp_path):
    for id,hp in [('dnd2014',1),('dnd2024',2)]:
        hero=Hero(name='Герой',hp=1,max_hp=12,abilities=dict(strength=10,dexterity=10,constitution=1,intelligence=10,wisdom=10,charisma=10))
        state={'hero':hero.model_dump()};RulesetEngine(BUILTINS[id]).rest(state,'short',hit_dice=1,rng=Dice(1))
        assert state['hero']['hp']==hp and state['hero']['hit_dice_available']==0

def test_custom_presets_are_versioned_safe_and_pinned(tmp_path):
    store=Store(tmp_path/'rules.sqlite3');library=RulesetRepository(store)
    custom=Ruleset(id='custom-world',name='Мой мир',proficiency_by_level=[5]*20,notes="__import__('os').remove('anything')",requires_implementation=['Телепортация'])
    one=library.save(custom);assert one.version==1
    cid=store.create('Мир',Hero(name='Герой'),one)['id']
    two=library.save(custom.model_copy(update={'proficiency_by_level':[7]*20}));assert two.version==2
    assert active(store.campaign(cid)['state']).proficiency_by_level[0]==5
    assert len(library.history(custom.id))==2
    with pytest.raises(ValueError):library.save(BUILTINS['dnd2024'])
    with pytest.raises(ValueError):Ruleset.model_validate({**custom.model_dump(),'python_code':'print(1)'})
    assert RulesetEngine(one).sheet(Hero(name='Герой'))['proficiency']==5

def test_conversion_preview_approval_checkpoint_stale_and_replay(tmp_path):
    store=Store(tmp_path/'convert.sqlite3');cid=store.create('Мир',Hero(name='Герой',exhaustion=2))['id'];library=RulesetRepository(store)
    before=store.campaign(cid);preview=library.preview(cid,'dnd2014')
    assert store.campaign(cid)==before and {r['field'] for r in preview['changes']}>={'speed','penalty','surprise'}
    with pytest.raises(ValueError):library.apply(cid,preview['approval'],False)
    applied=library.apply(cid,preview['approval'],True)
    assert active(store.campaign(cid)['state']).edition=='2014'
    assert library.apply(cid,preview['approval'],True)['event']['id']==applied['event']['id']
    assert applied['checkpoint'] in [r['id'] for r in store.saves(cid)]
    store.undo(cid);assert store.campaign(cid)['state']==before['state']
    with pytest.raises(ValueError):library.apply(cid,preview['approval'],True)
    stale=library.preview(cid,'dnd2014')
    store.record_scenario(cid,'change','Жду',lambda state:[{'type':'scripted','text':'Ожидание'}])
    with pytest.raises(ValueError,match='изменилось'):library.apply(cid,stale['approval'],True)
    store.restore(cid,applied['checkpoint']);assert store.campaign(cid)['state']==before['state']

def test_conversion_never_rewrites_previous_rolls(tmp_path):
    store=Store(tmp_path/'rolls.sqlite3');cid=store.create('Мир',Hero(name='Герой'))['id']
    action=store.stage(cid,'roll','Проверка',Plan(summary='Проверка',steps=[dict(kind='check',text='Проверка',ability='strength',dc=12)]),0,{})
    event=store.apply(action['id'],Dice(10));old=copy.deepcopy(event)
    repo=RulesetRepository(store);preview=repo.preview(cid,'dnd2014');repo.apply(cid,preview['approval'],True)
    assert store.event(event['id'])==old
    assert store.action(action['id'])['metrics']['ruleset']['id']=='dnd2024'


def test_gm_proficiency_uses_the_pinned_custom_rules():
    from solo.gm import render,GmAnswer
    preset=Ruleset(id='world',name='Мой мир',proficiency_by_level=[5]*20)
    text=render(GmAnswer(topic='proficiency',answer=''),Hero(name='Герой'),preset)
    assert '+5' in text and 'Мой мир' in text and 'Пользовательский пресет' in text

def test_save_proficiency_and_exhaustion_do_not_leak_check_rules():
    hero=Hero(name='Герой',exhaustion=1,saving_throw_proficiencies=['constitution'])
    old=RulesetEngine(BUILTINS['dnd2014']).saving_throw(hero,'constitution',12,rng=Dice(10))
    new=RulesetEngine(BUILTINS['dnd2024']).saving_throw(hero,'constitution',12,rng=Dice(10))
    assert old['dice']==[10] and old['total']==13 and old['success']
    assert new['total']==11 and not new['success']
