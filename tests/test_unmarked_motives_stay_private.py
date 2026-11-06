"""Motives and biography reach the hero only when marked public."""
import os
os.environ['SOLO_TEST']='1'

from solo.world import public_npc


def test_an_unmarked_motivation_stays_private():
    npc = dict(id='n1', name='Ирина', character='Sells bread.',
               motivation='Needs coin for her brother.')
    assert 'motivation' not in public_npc(npc)


def test_an_unmarked_goal_stays_private():
    npc = dict(id='n2', name='Писарь', character='Writes letters.',
               goals=['Learn who forged the deed.'])
    assert 'goals' not in public_npc(npc)


def test_an_unmarked_biography_stays_private():
    npc = dict(id='n3', name='Староста', character='Keeps order.',
               biography='He signed the order himself.')
    assert 'biography' not in public_npc(npc)


def test_an_explicitly_public_motivation_is_shown():
    npc = dict(id='n4', name='Ирина', character='Sells bread.',
               motivation=dict(scope='public', text='Wants the fair to reopen.'))
    shown = public_npc(npc)
    assert shown['motivation']['text'] == 'Wants the fair to reopen.'