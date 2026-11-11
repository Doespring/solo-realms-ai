"""A place summary stays short enough to show and to carry."""
import os
os.environ['SOLO_TEST']='1'

import pytest
from pydantic import ValidationError

from solo.world import WorldLocation


def test_an_endless_place_fact_is_refused():
    with pytest.raises(ValidationError) as caught:
        WorldLocation(name='Двор', facts=['ф' * 1001])
    assert 'слишком длинное' in str(caught.value), caught.value


def test_an_endless_place_secret_is_refused():
    with pytest.raises(ValidationError) as caught:
        WorldLocation(name='Двор', secrets=['т' * 1001])
    assert 'слишком длинное' in str(caught.value), caught.value