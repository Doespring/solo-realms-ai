"""One turn allows at most three spoken lines."""
import pytest
from pydantic import ValidationError

from solo.narration import Narrative


def test_three_lines_are_allowed():
    reply = Narrative(description='У колодца.',
                      dialogue=[dict(speaker='Ирина', text=str(i)) for i in range(3)],
                      proposals=[])
    assert len(reply.dialogue) == 3


def test_a_fourth_line_is_refused():
    with pytest.raises(ValidationError):
        Narrative(description='У колодца.',
                  dialogue=[dict(speaker='Ирина', text=str(i)) for i in range(4)],
                  proposals=[])