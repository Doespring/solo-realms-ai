"""A map node stays on the board."""
import os
os.environ['SOLO_TEST']='1'

import pytest
from pydantic import ValidationError

from solo.world import WorldNode


def test_a_node_outside_the_board_is_refused():
    with pytest.raises(ValidationError) as caught:
        WorldNode(id='a', name='Двор', coordinates=[120, 5])
    assert 'вне диапазона' in str(caught.value), caught.value


def test_a_negative_node_is_refused():
    with pytest.raises(ValidationError) as caught:
        WorldNode(id='a', name='Двор', coordinates=[-1, 5])
    assert 'вне диапазона' in str(caught.value), caught.value