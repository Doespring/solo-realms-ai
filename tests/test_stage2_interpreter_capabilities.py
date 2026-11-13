"""Stage 2 GM seam only: selected spell statuses, public context, real mock wire."""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import json

import pytest

from solo.game import Game
from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store
from test_cloud import configured,sse
from test_nat20_prototype import build,encounter


@pytest.fixture(scope='module')
def native_context(tmp_path_factory):
    directory=tmp_path_factory.mktemp('stage2-interpreter')
    store=Store(directory/'game.sqlite3');api=Nat20GameEngine(store)
    unsupported=next(row['slug'] for row in api.catalog('spells',limit=200)['entries']
        if row['execution_status']=='narrative_or_unsupported_in_combat')
    selected=[unsupported,'bless','magic-missile']
    cid=api.create_experimental_campaign('Wizard',build=build('wizard'),prepared_spells=selected,
        encounters={'rats':encounter()},seed=5)['id']
    def secrets(state):
        state['facts'].append('SECRET_CANARY')
        state['locations'][0]['secrets']=['SECRET_CANARY']
        state['locations'][0]['npc']=[dict(id='private-npc',name='Hidden',present=False,
            known='SECRET_CANARY',private_knowledge=['SECRET_CANARY'])]
        return [dict(type='scripted')]
    store.record_scenario(cid,'secret-fixture','Synthetic private source',secrets)
    api.execute(cid,GameCommand(request_id='start',expected_version=store.campaign(cid)['version'],
        action=dict(kind='start_combat',encounter_id='rats')))
    return directory,store,cid,unsupported,selected


def test_compiler_copies_only_selected_spell_statuses_and_preserves_acl(native_context,monkeypatch):
    _,store,cid,unsupported,selected=native_context
    expected=Nat20GameEngine.capabilities(store.campaign(cid))['spell_execution']
    def forbidden(*args,**kwargs):raise AssertionError('Turn compiler must not enumerate catalogue')
    monkeypatch.setattr(Nat20GameEngine,'catalog',forbidden)
    context=Game(store,None).compiler.command_context(store.campaign(cid),query='Магия')
    assert context['game_snapshot']['combat']['active']
    assert context['capabilities']['spell_execution']==expected
    assert set(expected)==set(selected)
    assert expected[unsupported]=='narrative_or_unsupported_in_combat'
    assert expected['bless']=='blocked' and expected['magic-missile']=='native_candidate'
    assert {'hide','bless'} <= context['capabilities']['known_issues'].keys()
    assert 'native_action' in {r['name'] for r in context['engine_commands']}
    assert 'cast_spell' in context['capabilities']['native_operations']
    assert 'SECRET_CANARY' not in json.dumps(context)


def test_interpreter_wire_carries_local_limits_without_disabling_other_magic(native_context,tmp_path):
    _,store,cid,unsupported,selected=native_context
    bodies=[]
    def respond(request):
        body=json.loads(request.content);bodies.append(body)
        packet=json.loads(body['messages'][-1]['content']);caps=packet['context']['capabilities']
        assert set(caps['spell_execution'])==set(selected)
        assert 'SECRET_CANARY' not in json.dumps(body)
        system=body['messages'][0]['content']
        assert 'narrative_or_unsupported_in_combat' in system and 'capabilities.known_issues' in system
        assert 'Bless/Hide' in system and 'не всей магии' in system
        if packet['player_intent']=='magic-missile':
            assert caps['spell_execution']['magic-missile']=='native_candidate'
            value=dict(status='command',action=dict(kind='native_action',operation='cast_spell',
                spell_id='magic-missile',target_ids=['rat-a','rat-b','rat-b'],slot_level=1),message='')
        else:
            value=dict(status='unsupported',action=None,message='Эта конкретная возможность недоступна в текущем бою.')
        return sse([dict(choices=[dict(delta=dict(content=json.dumps(value,ensure_ascii=False)))])])
    llm,profile=configured(tmp_path,respond);profile.context_tokens=65536
    context=Game(store,llm).compiler.command_context(store.campaign(cid),query='Магия')
    before=store.campaign(cid)
    async def run():
        try:
            for text in (unsupported,'bless','hide'):
                assert (await llm.interpret_command(text,context)).status=='unsupported'
            allowed=await llm.interpret_command('magic-missile',context)
            assert allowed.status=='command' and allowed.action.spell_id=='magic-missile'
        finally:await llm.close()
    asyncio.run(run())
    assert len(bodies)==4 and all(m['prompt_version']=='gm_commands_v1' for m in llm.metrics)
    assert store.campaign(cid)==before
