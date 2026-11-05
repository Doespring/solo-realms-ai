"""Scenario 6: the player is free, and an unknown way is a question.

Built on the same imported world the existing world tests use, so the check is
about the rule and not about a hand-built package: a place the source wrote
without a road is a question about the way, never a forbidden intent.
"""
import os
os.environ['SOLO_TEST']='1'
import pytest
from test_world import setup,package,install,go
from solo.world_service import WorldCommand
from solo.world import ensure,project


def test_printed_road_is_walkable_and_its_time_is_the_printed_one(tmp_path):
    store,cid,service=setup(tmp_path)
    install(service,cid)
    preview=service.preview(cid,'Старая башня')
    assert (preview['minutes'],preview['meters'])==(30,2000)
    event=go(service,cid,'Старая башня')
    assert store.campaign(cid)['state']['location']=='Старая башня'
    assert store.campaign(cid)['state']['elapsed_minutes']==30
    assert 'Прибытие: Старая башня' in str(event)


def test_missing_road_asks_about_the_way_and_does_not_move_the_hero(tmp_path):
    store,cid,service=setup(tmp_path)
    install(service,cid)
    # The hero walks one way; a place on no printed road stays unreachable.
    go(service,cid,'Старая башня',key='first')
    before=store.campaign(cid)['state']['location']
    # A place that exists in the base world but has no road on this map.
    with pytest.raises(ValueError) as denied:
        go(service,cid,'Порт на краю света',key='second')
    message=str(denied.value)
    # An unknown way is a question, never a forbidden intent.
    assert 'неизвестно' in message or 'Уточните' in message or 'Куда' in message
    assert 'нельзя' not in message and 'запрещ' not in message
    assert store.campaign(cid)['state']['location']==before


def test_refusing_a_meeting_never_creates_a_battle_result(tmp_path):
    store,cid,service=setup(tmp_path)
    install(service,cid,package(events=[dict(id='ambush',after_minutes=20,location='Старая башня',
        kind='encounter',lethal_risk=True,encounter=dict(environment='ruins',seed=819),
        text='Засада на дороге')]))
    go(service,cid,'Берег реки')
    assert project(store.campaign(cid)['state'])['pending_encounter']
    event=service.execute(cid,WorldCommand(request_id='no',kind='decline_encounter'))
    state=store.campaign(cid)['state']
    assert ensure(state)['pending_encounter'] is None and not state.get('combat')
    text=str(event)
    assert 'отклонено' in text.lower()
    assert 'побед' in text
