"""A source speaks about its own edition only when the words say so."""
import sqlite3
from contextlib import contextmanager

import pytest

from solo.preparation_pipeline import edition_evidence, detect_edition

DOC = 'a' * 64


class Pages:
    """Minimal stand-in for AdventureLibrary: only the page table is read."""

    def __init__(self, pages):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('CREATE TABLE pages (document TEXT, number INTEGER, content TEXT)')
        for number, content in pages.items():
            self.connection.execute('INSERT INTO pages VALUES (?,?,?)', (DOC, number, content))

    @contextmanager
    def db(self):
        yield self.connection


def evidence(pages):
    return edition_evidence(Pages(pages), [DOC])


def test_absence_of_2024_is_not_evidence_of_2014():
    """The book never says its edition; silence must stay unknown, not a guess."""
    result = evidence({1: 'Нападение на город начинается ночью.', 2: 'Дракон улетает на рассвете.'})
    assert result['edition'] is None
    assert result['ambiguous'] is False
    assert result['markers'] == []


@pytest.mark.parametrize('text,edition', [
    ('Это приключение для пятой редакции правил.', 'dnd2014'),
    ('Книга написана для D&D 2024 и её SRD 5.2.', 'dnd2024'),
])
def test_an_explicit_marker_is_reported_with_its_page(text, edition):
    result = evidence({7: text})
    assert result['edition'] == edition
    assert result['pages'] == [7]
    assert result['markers'][0]['marker'].casefold() in text.casefold()


def test_two_claimed_editions_are_ambiguous_rather_than_picked():
    result = evidence({3: 'Совместимо с пятой редакции.', 4: 'Обновлено до D&D 2024.'})
    assert result['edition'] is None
    assert result['ambiguous'] is True


def test_no_documents_means_no_claim():
    assert detect_edition(Pages({1: 'D&D 2024'}), []) is None
