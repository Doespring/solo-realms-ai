"""A move or a bond that names no character is refused."""
import os
os.environ['SOLO_TEST']='1'

import pytest
from pydantic import ValidationError

from solo.world import WorldEvent


def test_an_npc_move_without_a_character_is_refused():
    with pytest.raises(ValidationError) as caught:
        WorldEvent(id='e2', kind='npc_move', target='Таверна')
    assert 'npc' in str(caught.value).lower(), caught.value


def test_a_relationship_change_without_a_character_is_refused():
    with pytest.raises(ValidationError) as caught:
        WorldEvent(id='e3', kind='relationship')
    assert 'npc' in str(caught.value).lower(), caught.value