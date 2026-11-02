"""A draft may not grow without bound.

solo/preparation.py Draft.bounded() refuses a draft whose serialised size
passes 50 000 characters. Replacing that test with False left sixteen
preparation tests green: they build small drafts, so the ceiling is never
touched.

The draft is stored, copied through the API and handed back to the UI; an
unbounded one is a slow delivery, not a story. Section 9.2.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.preparation import Draft


def test_a_small_draft_passes():
    Draft(name='Хроника', description='Короткая история.').bounded()


def test_an_oversized_draft_is_refused():
    # description has its own 12 000 ceiling; hero is a plain dict with none,
    # so the draft as a whole is what stops it.
    heavy = {'background': 'я' * 60000}
    with pytest.raises(ValueError, match='слишком велик'):
        Draft(name='Хроника', hero=heavy).bounded()
