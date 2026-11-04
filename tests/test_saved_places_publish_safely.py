"""A place with no read-aloud must not publish the book's private words.

Section 4 of the task: closed text must not become public merely because a
scene needs a description, and old saved results must not stay unsafe. The
second half is the point of these tests: whatever a place was saved with, the
public text is derived on read, so no rewrite of the saved data is needed.

The defect was reproduced with the tower paragraph from the task: the whole
paragraph, including the hidden key, appeared as the public description.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.prepared_world import observable_opening
from solo.world import WorldLocation

TOWER = ('Башня. За северной стеной спрятан тайный ключ. '
         'Игроки об этом не знают.')


def place(**extra):
    return WorldLocation(name='Башня', description='', sealed_text=TOWER, **extra)


def test_a_saved_place_publishes_only_the_place_itself():
    opening = observable_opening(place())
    assert 'Башня' in opening
    assert 'ключ' not in opening and 'спрятан' not in opening, opening


def test_the_private_text_is_still_available_internally():
    # The GM needs the book's words; they stay on the record, just not public.
    assert place().sealed_text == TOWER


def test_an_empty_description_is_not_filled_from_the_secret():
    opening = observable_opening(place())
    assert opening.strip() != TOWER
    assert len(opening) < len(TOWER)


def test_a_read_aloud_box_is_what_becomes_public():
    p = WorldLocation(name='Башня', description='', sealed_text=TOWER,
                      facts=['Вы входите в тёмный зал. На полу лежит пепел.'])
    opening = observable_opening(p)
    assert 'ключ' not in opening, 'the box never carries the hidden key'


def test_the_projection_is_repeatable():
    # Deriving on read means old rows need no migration: the same saved place
    # yields the same safe text every time it is opened.
    p = place()
    first = observable_opening(p)
    second = observable_opening(WorldLocation(**p.model_dump()))
    assert first == second
