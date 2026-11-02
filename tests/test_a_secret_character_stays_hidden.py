"""A secret character does not appear in what the hero is shown."""
import os
os.environ['SOLO_TEST']='1'

from solo.world import public_npc


def test_a_secret_character_stays_hidden():
    npc = dict(id='n1', name='Наёмник', character='A quiet stranger.',
               scope='secret')
    assert public_npc(npc) is None


def test_a_masters_character_stays_hidden():
    npc = dict(id='n2', name='Соглядатай', character='Watches the yard.',
               visibility='gm')
    assert public_npc(npc) is None


def test_a_character_the_hero_has_not_met_stays_hidden():
    npc = dict(id='n3', name='Писарь', character='Writes letters.',
               known_by=['other'])
    assert public_npc(npc) is None


def test_a_public_character_is_shown():
    npc = dict(id='n4', name='Ирина', character='Sells bread.')
    shown = public_npc(npc)
    assert shown['name'] == 'Ирина'
    assert 'id' in shown