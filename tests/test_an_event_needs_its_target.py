"""An event that points somewhere must name where it points."""
import os
os.environ['SOLO_TEST']='1'

import pytest
from pydantic import ValidationError

from solo.world import WorldEvent


def test_a_reveal_without_a_target_is_refused():
    with pytest.raises(ValidationError) as caught:
        WorldEvent(id='e1', kind='reveal', text='Что-то видно')
    assert 'целев' in str(caught.value).lower(), caught.value