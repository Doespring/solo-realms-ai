"""An NPC record has a length ceiling."""
import os
os.environ['SOLO_TEST']='1'

import pytest
from pydantic import ValidationError

from solo.world import WorldNPC


def test_an_npc_goal_cannot_be_endless():
    with pytest.raises(ValidationError) as caught:
        WorldNPC(name='Ирина', goals=['ц' * 1001])
    assert 'слишком длинная' in str(caught.value), caught.value


def test_an_npc_secret_cannot_be_endless():
    with pytest.raises(ValidationError) as caught:
        WorldNPC(name='Ирина', private_knowledge=['т' * 1001])
    assert 'слишком длинная' in str(caught.value), caught.value