"""Generated content must not pass for facts of the attached book."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.prepared_world import PreparedWorldValidator


class Library:
    def db(self):
        return None


def test_generated_content_is_not_book_fact():
    validator = PreparedWorldValidator(Library(), ["doc"])
    entity = type("E", (), {"origin": "original", "evidence": []})()
    with pytest.raises(ValueError) as caught:
        validator.evidence(entity)
    assert 'Генерация' in str(caught.value), caught.value


def test_generated_content_is_allowed_when_asked_for():
    validator = PreparedWorldValidator(Library(), ["doc"], original=True)
    entity = type("E", (), {"origin": "original", "evidence": []})()
    assert validator.evidence(entity) == []


def test_original_entity_cannot_fake_a_source():
    validator = PreparedWorldValidator(Library(), ["doc"], original=True)
    entity = type("E", (), {"origin": "original", "evidence": ["придумано"]})()
    with pytest.raises(ValueError) as caught:
        validator.evidence(entity)
    assert 'выдуманного' in str(caught.value), caught.value

class Database:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args):
        return None


class LibraryWithDb(Library):
    def db(self):
        return Database()


def test_a_fact_without_origin_is_refused():
    validator = PreparedWorldValidator(LibraryWithDb(), ["doc"])
    entity = type("E", (), {"origin": "book", "evidence": []})()
    with pytest.raises(ValueError) as caught:
        validator.evidence(entity)
    assert caught.value is not None


def test_a_fact_from_an_unselected_document_is_refused():
    validator = PreparedWorldValidator(LibraryWithDb(), ["doc"])
    proof = type("P", (), {"document": "other", "page": 1, "quote": "x"})()
    entity = type("E", (), {"origin": "book", "evidence": [proof]})()
    with pytest.raises(ValueError) as caught:
        validator.evidence(entity)
    assert 'Источник' in str(caught.value), caught.value

class RowDb(Database):
    def __init__(self, row):
        self.row = row

    def execute(self, *args):
        return self

    def fetchone(self):
        return self.row


class LibraryWithRow(Library):
    def __init__(self, row):
        self.row = row

    def db(self):
        return RowDb(self.row)


def test_a_citation_needs_a_real_page():
    validator = PreparedWorldValidator(LibraryWithRow(None), ["doc"])
    proof = type("P", (), {"document": "doc", "page": 99, "quote": "x"})()
    entity = type("E", (), {"origin": "book", "evidence": [proof]})()
    with pytest.raises(ValueError) as caught:
        validator.evidence(entity)
    assert 'Цитата' in str(caught.value), caught.value
