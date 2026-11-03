import copy,json
import pytest
from solo.first_playable import guard_plan,guard_text,entry_result,BOUNDARY,HOARD
from solo.contracts import Plan,Step
from solo.prepared_world import PreparedWorldValidator
from solo.adventure_library import AdventureLibrary

def staged():return dict(adventure=dict(definition=dict(entry_stage=dict(policy='hoard-entry-v1'))),deferred=[])

@pytest.mark.parametrize('text',['Иду в Гринест','Жду','Осматриваюсь и атакую','Осматриваю дорогу; затем иду','Рассматриваю крепость, если она пуста, вхожу','Открываю дверь','Говорю с Леосином'])
def test_unprepared_dependencies_stop_before_execution(text):
    assert guard_text(staged(),text)==BOUNDARY
    with pytest.raises(ValueError):guard_plan(staged(),Plan(summary='Bad interpreter',steps=[Step(kind='observe',text=text)]))

@pytest.mark.parametrize('step',[Step(kind='move',text='Осматриваюсь',target='Гринест'),Step(kind='wait',text='Осматриваюсь'),Step(kind='observe',text='Осматриваюсь',disposition='conditional',condition='позже')])
def test_semantic_label_and_deferred_steps_cannot_bypass_stage(step):
    with pytest.raises(ValueError):guard_plan(staged(),Plan(summary='test',steps=[step]))

def test_observations_are_allowed_but_old_campaigns_have_no_new_global_gate():
    s=staged();p=Plan(summary='Осмотр',steps=[Step(kind='observe',text='Осматриваюсь')]);guard_plan(s,p);assert guard_text(s,'Осматриваюсь') is None
    s['deferred']=[dict(kind='move',text='Иду в город')]
    with pytest.raises(ValueError):guard_plan(s,p)
    assert guard_text({},'Иду в Гринест') is None

def test_unknown_book_quarantine_is_not_approved(tmp_path):
    lib=AdventureLibrary(tmp_path);result=dict(ready=False,quarantine=[dict(kind='npc',essential=True)],definition={},provenance={})
    got=entry_result(lib,{'a'*64},result,'dnd2014','entry')
    assert not got['ready'] and not got['entry_readiness']['ready'] and got['quarantine']==result['quarantine']

def test_pinned_source_missing_never_starts_and_edition_mismatch_is_reported(tmp_path):
    lib=AdventureLibrary(tmp_path);result=dict(ready=False,quarantine=[],definition={},provenance={})
    assert not entry_result(lib,{HOARD},result,'dnd2014','entry')['ready']
    # A source-edition mismatch is a fact about the book, not a blocker: the entry scene
    # carries no mechanics. The missing pinned blob still prevents any start.
    mismatched=entry_result(lib,{HOARD},result,'dnd2024','entry')
    assert not mismatched['ready']
    assert 'Исходный PDF отсутствует' in mismatched['entry_readiness']['reason']
    # Another document is not the pinned one, so no entry stage is ever produced.
    assert not entry_result(lib,{'b'*64},result,'dnd2014','entry')['ready']

def test_exact_duplicate_and_explicit_npc_binding_are_repaired_without_guessing(tmp_path):
    from solo.preparation_contracts import PreparationBatch
    lib=AdventureLibrary(tmp_path)
    with lib.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',('a'*64,'.txt',100,'extracted',1))
        db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',('a'*64,1,'1','Башня. Ирина находится в Башня. Лев упомянут рядом с Башня.'))
    proof=[dict(document='a'*64,page=1,quote='Башня. Ирина находится в Башня. Лев упомянут рядом с Башня.')]
    batch=PreparationBatch(ruleset='dnd2014',start='Башня',entities=[dict(key='a',kind='location',data=dict(name='Башня'),evidence=proof),dict(key='b',kind='location',data=dict(name='башня'),evidence=proof),dict(key='n',kind='npc',data=dict(name='Ирина'),evidence=proof),dict(key='u',kind='npc',data=dict(name='Лев'),evidence=proof)])
    result=PreparedWorldValidator(lib,{'a'*64}).compile([batch.model_dump()],'dnd2014')
    assert len(result['definition']['world']['locations'])==1
    assert [n['name'] for n in result['definition']['world']['locations'][0]['npc']]==['Ирина']
    # The reason was reworded when the quarantine started saying what is
    # actually proven: the source never placed this NPC. Asserting the sentence
    # only froze a wording, so check the key and that the reason is about place.
    assert any(q['key']=='u' and 'местонахожд' in q['reason'].lower() for q in result['quarantine'])
    assert not any(q.get('key') in ('b','n') for q in result['quarantine'])
