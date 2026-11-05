"""A road the hero has not learned must not be walked.

solo/travel.py:50-51 skips routes that are not marked known, or whose ends are
unknown places. Deleting those two guards left four travel tests green: they
describe scenarios where every route is already known, so nothing was being
filtered.

Section 7.1: the hero travels the geography he has actually discovered.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.travel import itinerary


def _state(route_known=True, place_known=True):
    return {'location': 'Тракт',
            'hero': {'name': 'Мирон', 'hp': 12, 'max_hp': 12,
                     'abilities': {k: 12 for k in ('strength', 'dexterity',
                                                   'constitution', 'intelligence',
                                                   'wisdom', 'charisma')}},
            'world': {'settings': {'mode': 'story'}},
            'locations': [{'name': 'Тракт', 'known_to_hero': True},
                          {'name': 'Застава', 'known_to_hero': place_known}],
            'geography': {'nodes': [{'id': 'n1', 'name': 'Тракт', 'known': True},
                                    {'id': 'n2', 'name': 'Застава', 'known': place_known}],
                          'routes': [{'from': 'n1', 'to': 'n2', 'minutes': 30,
                                      'meters': 900, 'known': route_known}]}}


def test_a_known_road_is_walkable():
    path, _ = itinerary(_state(), 'Застава')
    assert path, 'the known road was refused'


def test_an_unlearned_road_is_not_walkable():
    with pytest.raises(ValueError):
        itinerary(_state(route_known=False), 'Застава')


def test_a_road_to_an_unknown_place_is_not_walkable():
    with pytest.raises(ValueError):
        itinerary(_state(place_known=False), 'Застава')
