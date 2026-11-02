"""Small prototype-2 stubs exercise the real cloud protocol, never a paid API."""
import os
os.environ['SOLO_TEST'] = '1'

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from solo.ai_roles import routerai_preset
from solo.cloud import CloudLLM
from solo.cloud_cost import CostLedger
from solo.cloud_settings import CloudSettings
from solo.contracts import Hero
from solo.game import Game
from solo.narrative_grounding import fallback
from solo.storage import Store


@pytest.mark.parametrize('topic,answer', [
    ('Спрашиваю Дорна про мост', 'Мост закрыт после дождя.'),
    ('Спрашиваю Дорна про постоялый двор', 'Постоялый двор открыт.'),
])
def test_dialogue_uses_scoped_knowledge_on_topic_and_rejects_state_audit(topic, answer):
    from solo.prototype_gm import dialogue_memories
    from solo.narrative_grounding import CanonViolation, verify
    records = [dict(id=1, kind='npc_state', truth='fact', content='Дорн: изменено goals. []'),
               dict(id=2, kind='npc_knowledge', truth='fact', provenance='scenario', source_ref='preparation:test',
                    content='Описание: Кузнец встречает гостей.\nЗнание: Мост закрыт после дождя. Постоялый двор открыт.'),
               dict(id=3, kind='npc_knowledge', truth='statement', content='У моста лежит золото.')]
    memories = dialogue_memories(records, topic)
    assert [m['id'] for m in memories] == [2] and memories[0]['source_ref'] == 'preparation:test'
    packet = dict(npc=[dict(name='Дорн', memory=memories)], player_words=[dict(to='Дорн', words=topic)])
    value = fallback(packet)
    assert value['dialogue'] == [dict(speaker='Дорн', text=answer)]
    verify(value, packet)
    with pytest.raises(CanonViolation):
        verify(dict(description='', dialogue=[dict(speaker='Дорн', text=records[0]['content'])], proposals=[]), packet)
    packet['npc'][0]['memory'] = dialogue_memories([records[0]], topic)
    assert fallback(packet)['dialogue'][0]['text'] == 'Не знаю.'


class Vault:
    def get(self, profile):return 'mock-prototype-2'
    def exists(self, profile):return True


def fixture(directory, *, hero_class='rogue', place='Кузница', person='Дорн', know='Мост закрыт.'):
    store = Store(directory / 'game.sqlite3')
    state = dict(location=place,turn=0,facts=[],deferred=[],locations=[dict(name=place,
                 description=place+' открыта.',objects=['следы'],facts=[],
                 npc=[dict(id='person',name=person,known=know,character='Немногословный местный житель.')],
                 checks={'traces':dict(ability='wisdom',dc=12)})])
    cid = store.create('prototype-2',Hero(name='Путник',class_name=hero_class),initial_state=state)['id']
    return store,cid


def cloud(directory, choose):
    calls=[]
    def respond(request):
        packet=json.loads(json.loads(request.content)['messages'][-1]['content'])
        calls.append(packet)
        value=choose(packet) if 'player_intent' in packet else fallback(packet)
        frames=[dict(choices=[dict(delta=dict(content=json.dumps(value,ensure_ascii=False)))])]
        body=''.join('data: '+json.dumps(f,ensure_ascii=False)+'\n\n' for f in frames)+'data: [DONE]\n\n'
        return httpx.Response(200,content=body.encode(),headers={'content-type':'text/event-stream'})
    profile=routerai_preset();profile.enabled=True
    for key in ('max_request_cost','session_budget','daily_budget','monthly_budget','preparation_budget'):setattr(profile,key,Decimal(10))
    for cfg in (profile.interpreter,profile.narrator):cfg.input_per_million=cfg.output_per_million=Decimal(1)
    settings=CloudSettings(directory/'profiles.json',Vault());settings.save(profile)
    llm=CloudLLM(settings,CostLedger(directory/'cost.sqlite3'),client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    return llm,calls


def test_two_worlds_social_and_observe_reuse_real_transport_without_state_mutation(tmp_path):
    async def run():
        for index, (klass,place,person,know) in enumerate([
            ('rogue','Кузница','Дорн','Мост закрыт.'),('wizard','Обсерватория','Эна','Сегодня видна комета.')]):
            directory=tmp_path/f'prototype-2-{index}';directory.mkdir()
            store,cid=fixture(directory,hero_class=klass,place=place,person=person,know=know)
            llm,calls=cloud(directory,lambda p:dict(status='observe',target='') if 'осматр' in p['player_intent'].casefold() else dict(status='conversation',target='person'))
            try:
                game=Game(store,llm);before=store.campaign(cid)['state']
                observation=await game.play_command(cid,'look','Осматриваюсь',0)
                assert observation['status']=='observation' and place in observation['narrative']
                speech=await game.play_command(cid,'talk','Спрашиваю местного жителя',store.campaign(cid)['version'])
                assert speech['status']=='conversation' and know in speech['narrative']
                after=store.campaign(cid)['state']
                assert after['hero']==before['hero'] and after['facts']==before['facts']
                assert len(store.history(cid))==2
                first=calls[0]['context']
                assert first['sheet']['class_name']==klass and first['scene']['name']==place
                assert first['engine_commands'] and first['memory_context']
                count=len(calls)
                restarted=Game(Store(store.path),llm)
                replay=await restarted.play_command(cid,'talk','Спрашиваю местного жителя',0)
                assert replay['narrative']==speech['narrative'] and len(calls)==count
                assert restarted.compiler.command_context(store.campaign(cid),query='Спрашиваю')['memory_context']['memories']
            finally:await llm.close()
    asyncio.run(run())


def test_new_action_after_clarification_or_engine_rejection_never_sticks(tmp_path):
    directory=tmp_path/'prototype-2';directory.mkdir()
    store,cid=fixture(directory)
    def choose(p):
        latest=p['player_intent'].rsplit('\nУточнение игрока: ',1)[-1]
        if latest=='Атакую':return dict(status='clarification',action=None,message='Кого атаковать?')
        if latest=='Осматриваюсь':return dict(status='observe',target='')
        return dict(status='command',action=dict(kind='check',check_id='missing'),message='')
    llm,calls=cloud(directory,choose)
    async def run():
        try:
            game=Game(store,llm)
            assert (await game.play_command(cid,'ask','Атакую',0))['status']=='clarification'
            game=Game(Store(store.path),llm)
            assert (await game.play_command(cid,'look','Осматриваюсь',0))['status']=='observation'
            assert store.open_question(cid) is None
            assert (await game.play_command(cid,'bad','Проверяю несуществующее',store.campaign(cid)['version']))['status']=='rejected'
            assert (await game.play_command(cid,'again','Осматриваюсь',store.campaign(cid)['version']))['status']=='observation'
            assert store.campaign(cid)['version']==2
        finally:await llm.close()
    asyncio.run(run())


def test_prepared_native_world_lookup_mechanics_and_restart(tmp_path):
    from solo.preparation import Preparation, Draft
    from solo.prototype_prepared import launch_prepared
    from solo.nat20_api import Nat20GameEngine
    from test_nat20_prototype import build, encounter
    async def run():
        for index,(klass,place) in enumerate([('rogue','Рыночная площадь'),('wizard','Звёздная башня')]):
            directory=tmp_path/f'prototype-2-native-{index}';directory.mkdir()
            store=Store(directory/'game.sqlite3');preparation=Preparation(directory,store)
            draft=preparation.create()
            body=Draft.model_validate(dict(draft['body'],prepared=True,background_confirmed=True,
                 name=place,start_name=place,opening=place+' освещена.',hero=dict(name='Искатель',class_name=klass)))
            saved=preparation.save(draft['id'],body,draft['revision'])
            result=launch_prepared(preparation,draft['id'],confirm=True,expected_revision=saved['revision'],
                build=build(klass),encounters={'rats':encounter()},prepared_spells=['magic-missile','bless'] if klass=='wizard' else [],seed=5)
            cid=result['campaign']['id']
            assert result['campaign']['state']['location']==place
            assert result['campaign']['state']['hero']['class_name']==klass
            assert not result['campaign']['state'].get('lantern_scene')
            def choose(packet):
                text=packet['player_intent']
                if 'каталог' in text and 'catalog_details' not in packet['context']:
                    return dict(status='lookup',ids=['spells:magic-missile'])
                if 'проверяю' in text.casefold():
                    return dict(status='command',action=dict(kind='check',check_id='save-dex'),message='')
                if 'начинаю' in text.casefold():
                    return dict(status='command',action=dict(kind='start_combat',encounter_id='rats'),message='')
                if 'Bless' in text:
                    return dict(status='command',action=dict(kind='native_action',operation='cast_spell',spell_id='bless',target_ids=['hero'],slot_level=1),message='')
                if 'Снаряды' in text:
                    return dict(status='command',action=dict(kind='native_action',operation='cast_spell',spell_id='magic-missile',target_ids=['rat-a','rat-b','rat-b'],slot_level=1),message='')
                if 'Уклоняюсь' in text:
                    return dict(status='command',action=dict(kind='native_action',operation='dodge'),message='')
                return dict(status='observe',target='')
            llm,calls=cloud(directory,choose)
            try:
                game=Game(store,llm)
                before=store.campaign(cid)['state']['nat20']
                observed=await game.play_command(cid,'look','Осматриваюсь',store.campaign(cid)['version'])
                assert observed['status']=='observation'
                assert store.campaign(cid)['state']['nat20']==before
                context=calls[0]['context']
                assert context['capabilities']['experimental'] and context['encounters']==['rats']
                assert 'native_action' in {row['name'] for row in context['engine_commands']}
                assert 'cast_spell' in context['capabilities']['native_operations']
                lookup=await game.play_command(cid,'lookup','Открываю каталог',store.campaign(cid)['version'])
                assert lookup['status']=='observation'
                searched=[p for p in calls if p.get('context',{}).get('catalog_details')]
                assert searched and len(searched[0]['context']['catalog_details'])==1
                checked=await game.play_command(cid,'check','Проверяю ловкость',store.campaign(cid)['version'])
                assert checked['status']=='resolved' and checked['events'][0]['id'].startswith('nat20:')
                started=await game.play_command(cid,'start','Начинаю бой',store.campaign(cid)['version'])
                assert started['status']=='resolved'
                if klass=='wizard':
                    before=store.campaign(cid)['state']['nat20']
                    failed=await game.play_command(cid,'broken','Применяю Bless',store.campaign(cid)['version'])
                    assert failed['status']=='rejected' and store.campaign(cid)['state']['nat20']==before
                action=await game.play_command(cid,'native','Снаряды' if klass=='wizard' else 'Уклоняюсь',store.campaign(cid)['version'])
                assert action['status']=='resolved'
                if klass=='wizard':assert store.campaign(cid)['state']['nat20']['hero']['spell_slots']['1']==1
                restarted=Game(Store(store.path),llm)
                replay=await restarted.play_command(cid,'check','Проверяю ловкость',0)
                assert replay['events']==checked['events']
                assert Nat20GameEngine(restarted.store).restore(cid)['runtime']==store.campaign(cid)['state']['nat20']
            finally:await llm.close()
    asyncio.run(run())
