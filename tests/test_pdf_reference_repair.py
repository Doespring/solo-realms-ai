"""The user-observed PDF failure: reference fields arrived as objects.

A real book run stopped at 36/38 blocks because some blocks returned
``start`` and ``entities[].location`` as ``{"key"|"id"|"name": ...}`` objects
instead of the exact string the contract demands. The saved 36 blocks must
survive, the remaining blocks must be repaired locally (no second paid call),
and an ambiguous reference must stay a visible, retryable failure.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.preview_mock import PreviewMock
from solo.prepared_world import PreparedWorldValidator
from solo.adventure_library import AdventureLibrary
from solo.preparation_contracts import PreparationBatch


def block(start, entities):
    return dict(title='Глава', ruleset='dnd2014', start=start, entities=entities)


def location(key, name):
    return dict(key=key, kind='location', origin='original', data=dict(name=name, description='Место из книги.'))


def npc(key, name, place):
    return dict(key=key, kind='npc', location=place, origin='original', data=dict(name=name, character='Проводник.'))


# --- contract level -------------------------------------------------------

def test_object_start_and_location_are_repaired_to_exact_names():
    batch = PreparationBatch.model_validate(block({'name': 'Мост'}, [
        location('bridge', 'Мост'), npc('guide', 'Ирина', {'name': 'Мост'})]))
    assert batch.start == 'Мост'
    assert batch.entities[1].location == 'Мост'


@pytest.mark.parametrize('reference', [{'key': 'bridge'}, {'id': 'bridge'}, {'name': 'Мост'}, {'key': 'bridge', 'name': 'Мост'}])
def test_every_documented_reference_shape_resolves_when_unambiguous(reference):
    batch = PreparationBatch.model_validate(block(reference, [location('bridge', 'Мост')]))
    assert batch.start == 'Мост'


def test_null_becomes_an_empty_string_rather_than_a_type_error():
    batch = PreparationBatch.model_validate(block(None, [
        location('bridge', 'Мост'), npc('guide', 'Ирина', None)]))
    assert batch.start == ''
    assert batch.entities[1].location == ''


def test_ambiguous_reference_is_rejected_with_an_explained_message():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match='Ссылка локации не определена однозначно'):
        PreparationBatch.model_validate(block({'name': 'Нигде'}, [
            location('bridge', 'Мост'), location('tower', 'Башня')]))


def test_unknown_object_shape_is_rejected_not_silently_dropped():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PreparationBatch.model_validate(block({'id': 'x', 'city': 'Мост'}, [location('bridge', 'Мост')]))


def test_a_plain_string_reference_is_untouched():
    batch = PreparationBatch.model_validate(block('Мост', [npc('guide', 'Ирина', 'Мост')]))
    assert batch.start == 'Мост' and batch.entities[0].location == 'Мост'
