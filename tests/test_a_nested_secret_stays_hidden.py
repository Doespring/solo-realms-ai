"""A nested secret inside a character never reaches the hero."""
import os
os.environ['SOLO_TEST']='1'

from solo.world import public_npc


def test_a_secret_field_inside_a_character_is_dropped():
    npc = dict(id='n1', name='Ирина', character='Sells bread.',
               relationships=dict(hero=dict(trust=2, secrets=['He took the coin.'])))
    shown = public_npc(npc)
    assert 'He took the coin.' not in str(shown)


def test_a_gm_only_field_inside_a_character_is_dropped():
    npc = dict(id='n2', name='Писарь', character='Writes letters.',
               relationships=dict(hero=dict(trust=1, gm_only='Will betray at dawn.')))
    shown = public_npc(npc)
    assert 'Will betray' not in str(shown)


def test_a_public_field_inside_a_character_survives():
    npc = dict(id='n3', name='Ирина', character='Sells bread.',
               relationships=dict(hero=dict(trust=2)))
    shown = public_npc(npc)
    assert shown['relationships']['hero']['trust'] == 2