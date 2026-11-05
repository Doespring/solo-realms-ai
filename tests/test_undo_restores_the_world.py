"""Undoing a turn must put the world back, not just hide the row.

solo/storage.py:551 restores before_state and bumps the version. Replacing that
write with pass left both undo tests green: they check the search index and the
narration guard, never the state the player sees. Section 11: undo is a real
rollback of the world.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.storage import Store


def _turn(store, cid, request_id, place):
    return store.record_scenario(cid, request_id, 'Иду в ' + place,
                                 lambda state: [dict(type='scripted', text='Готово.')],
                                 )


def test_undo_restores_the_previous_state(tmp_path):
    store = Store(tmp_path / 'undo.sqlite3')
    cid = store.create('Откат', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    before = store.campaign(cid)['state']
    before_mark = before.get('location')

    def move(state):
        state['location'] = 'Другое место'
        return [dict(type='scripted', text='Вы пришли.')]

    store.record_scenario(cid, 'walk-1', 'Иду в другое место', move)
    assert store.campaign(cid)['state']['location'] == 'Другое место'

    store.undo(cid)
    after = store.campaign(cid)['state']
    assert after.get('location') == before_mark, 'the world did not roll back'


def test_undo_bumps_the_version_so_the_page_notices(tmp_path):
    store = Store(tmp_path / 'undo2.sqlite3')
    cid = store.create('Откат', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    store.record_scenario(cid, 'walk-1', 'Осматриваюсь',
                          lambda state: [dict(type='scripted', text='Тихо.')])
    version = store.campaign(cid)['version']
    store.undo(cid)
    assert store.campaign(cid)['version'] > version, 'no version change'
