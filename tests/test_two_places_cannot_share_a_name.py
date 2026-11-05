"""Two places cannot share a name, and two map nodes cannot share an id."""
import os
os.environ['SOLO_TEST']='1'

import pytest
from pydantic import ValidationError

from solo.world import ScenarioPackage, WorldLocation


def _place(name):
    return WorldLocation(name=name)


def test_two_places_cannot_share_a_name():
    with pytest.raises(ValidationError) as caught:
        ScenarioPackage(name='Пакет', source='автор', locations=[_place('Двор'), _place('Двор')])
    assert 'Повтор' in str(caught.value), caught.value


def test_two_map_nodes_cannot_share_an_id():
    with pytest.raises(ValidationError) as caught:
        ScenarioPackage(name='Пакет', source='автор', nodes=[{'id': 'a', 'name': 'Двор', 'coordinates': [0, 0]},
                     {'id': 'a', 'name': 'Сад', 'coordinates': [1, 0]}])
    assert 'Повтор' in str(caught.value), caught.value