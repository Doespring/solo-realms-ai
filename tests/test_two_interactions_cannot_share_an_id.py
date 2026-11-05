"""Two interactions in one place cannot share an id."""
import os
os.environ['SOLO_TEST']='1'

import pytest
from pydantic import ValidationError

from solo.world import WorldLocation


def _interaction(i, name):
    return {'id': i, 'name': name, 'kind': 'door',
            'destination': 'Сад', 'accessible': True}


def test_two_interactions_cannot_share_an_id():
    with pytest.raises(ValidationError) as caught:
        WorldLocation(
            name='Двор',
            interactions=[_interaction('d1', 'Дверь'),
                          _interaction('d1', 'Люк')])
    assert 'Повтор' in str(caught.value), caught.value