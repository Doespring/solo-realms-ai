"""A room name must be present and short enough to speak."""
import os
os.environ['SOLO_TEST']='1'

import pytest
from pydantic import ValidationError

from solo.world import WorldLocation


def test_an_empty_room_name_is_refused():
    with pytest.raises(ValidationError) as caught:
        WorldLocation(name='Двор', aliases=['   '])
    assert 'помещени' in str(caught.value).lower(), caught.value


def test_an_endless_room_name_is_refused():
    with pytest.raises(ValidationError) as caught:
        WorldLocation(name='Двор', aliases=['я' * 121])
    assert 'помещени' in str(caught.value).lower(), caught.value