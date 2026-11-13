'''A look-around with nothing new must not replay the scene.

Live data (campaign 74a716f6) stored the same 83-character paragraph for
seven different look-arounds, because the fallback narrator joined the scene
description and dropped the outcome. An observation now says plainly that
nothing new was noticed, while a real outcome still renders.
'''
import sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solo.narrative_grounding import fallback, OBSERVATION  # noqa: E402

SCENE = 'У старого фонаря стоит домик. Смотрительница Лада просит проверить следы.'


def packet(**extra):
    base = {'place': 'Двор', 'atmosphere': SCENE, 'confirmed': [], 'npc': [], 'memory': []}
    base.update(extra)
    return base


def test_empty_observation_does_not_repeat_the_scene():
    value = fallback(packet(observation=True))
    # The place stays named: a look-around answers 'where am I', and dropping
    # the name left the player without that answer.
    assert value['description'].endswith(OBSERVATION)
    assert value['description'] != SCENE


def test_a_cut_off_fragment_never_reaches_the_player():
    """A 53-character fragment passed the old 'shorter than 60' test and the
    table read '... по дороге, лениво Нового ничего не заметно.'"""
    value = fallback(packet(observation=True,
                            description_sources=['Уже несколько дней вы путешествуете по дороге, лениво']))
    assert 'лениво' not in value['description']
    assert not value['description'].endswith(', лениво — ' + OBSERVATION)


def test_the_place_is_named_on_its_own_line():
    value = fallback(packet(observation=True, place='Кузница', atmosphere='', confirmed=[]))
    assert value['description'] == 'Кузница — ' + OBSERVATION


def test_talking_still_describes_the_scene():
    assert SCENE in fallback(packet(observation=False))['description']


def test_observation_with_an_outcome_reports_it():
    value = fallback(packet(observation=True, outcomes=[{'outcome': 'успех'}]))
    assert value['description'].startswith('Действие удалось.')


def test_packet_carries_the_observation_flag():
    src = (ROOT / 'solo' / 'prototype_gm.py').read_text(encoding='utf-8')
    assert 'observation=not conversation' in src
