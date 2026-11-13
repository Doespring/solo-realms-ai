"""Russian mock phrase reaches the existing Nat20 Dash, without parser/rules changes."""
import asyncio
import socket
import sys

import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.command_engine import CommandEngine
from solo.game_commands import GameCommand,tool_schemas
from solo.nat20_api import Nat20GameEngine
from solo.preview_mock import PreviewMock
from solo.storage import Store


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv('SOLO_NAT20_BLESS_STAND',raising=False)
    original=socket.socket.connect
    def local(sock,address):
        if address[0] not in ('127.0.0.1','::1'):raise AssertionError('External/paid calls forbidden')
        return original(sock,address)
    monkeypatch.setattr(socket.socket,'connect',local)


def post(client,cid,rid,text,version):
    response=client.post(f'/api/campaigns/{cid}/command-turn',json=dict(request_id=rid,text=text,expected_version=version))
    assert response.status_code==200,response.text
    return response.json()


def test_russian_dash_after_old_question_native_retry_and_stale(tmp_path,monkeypatch):
    store=Store(tmp_path/'dash-phrase.sqlite3')
    engine=Nat20GameEngine(store)
    cid=engine.create_campaign('Dash phrase',seed=5,definition_id='fighter-standard-array-2024-v1')['id']
    engine.execute(cid,GameCommand(request_id='start',expected_version=store.campaign(cid)['version'],action=dict(kind='start_combat',encounter_id='basic')))
    def forbidden(*args,**kwargs):raise AssertionError('Nat20 cannot fall back to legacy mechanics')
    monkeypatch.setattr(CommandEngine,'resolve',forbidden)
    model=PreviewMock();version=store.campaign(cid)['version']
    with TestClient(create_app(store.path,model)) as client:
        assert post(client,cid,'unknown','Неизвестная mock-фраза',version)['status']=='clarification'
        result=post(client,cid,'dash-ru','Рывок',version)
        assert result['status']=='resolved'
        assert result['events'][0]['payload']['command']=='dash'
        assert result['state_version']==version+1
        assert store.open_question(cid) is None
        packet=next(p['packet'] for p in reversed(model.packets) if p['mode']=='interpreter')
        assert packet['player_intent']=='Неизвестная mock-фраза\nУточнение игрока: Рывок'
        before=store.campaign(cid);calls=len(model.packets)
        repeat=post(client,cid,'dash-ru','Рывок',version)
        assert repeat['events']==result['events'] and repeat['narrative_policy']=='replayed'
        assert post(client,cid,'stale','Рывок',version)['status']=='state_version_error'
        assert post(client,cid,'dash-ru','Осматриваю следы',version+1)['status']=='rejected'
        assert store.campaign(cid)==before and len(model.packets)==calls


def test_genuine_clarification_still_uses_context(tmp_path):
    store=Store(tmp_path/'genuine.sqlite3');model=PreviewMock()
    cid=Nat20GameEngine(store).create_campaign('Question',seed=5,definition_id='fighter-standard-array-2024-v1')['id']
    version=store.campaign(cid)['version']
    with TestClient(create_app(store.path,model)) as client:
        assert post(client,cid,'ask','Осматриваю',version)['status']=='clarification'
        answer=post(client,cid,'answer','следы',version)
        assert answer['status']=='resolved' and answer['events'][0]['payload']['command']=='check'
        assert store.open_question(cid) is None
        assert any(p['mode']=='interpreter' and p['packet']['player_intent']=='Осматриваю\nУточнение игрока: следы' for p in model.packets)


def test_exact_phrase_and_pending_magic_stays_hidden(monkeypatch):
    expected={'check','start_combat','move','dash','attack','end_turn','short_rest','long_rest','end_combat','use_feature','native_action','level_up','scene_action'}
    assert {t['function']['name'] for t in tool_schemas()}==expected
    async def inspect():
        model=PreviewMock()
        dash=await model.interpret_command('Рывок',{})
        assert dash.action.model_dump()=={'kind':'dash'}
        for phrase in ('Рывок и атака','Рывок на 60 футов'):
            assert (await model.interpret_command(phrase,{})).status=='clarification'
        assert (await model.interpret_command('Применяю Bless',{})).status=='unsupported'
    asyncio.run(inspect())
    monkeypatch.setenv('SOLO_TEST','1');monkeypatch.setenv('SOLO_NAT20_BLESS_STAND','1')
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    assert {t['function']['name'] for t in tool_schemas()}==expected
