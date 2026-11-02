"""A place typed with a different e/yo spelling is still recognised."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo import travel
from solo.contracts import Hero
from solo.storage import Store


def test_a_yo_spelling_finds_the_place(tmp_path, monkeypatch):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    state['locations'][0]['name'] = 'Тёмная заводь'
    state['location'] = 'Тёмная заводь'
    assert travel.destination('темная заводь', state) == 'Тёмная заводь', (
        'the spelling without yo did not reach the place')