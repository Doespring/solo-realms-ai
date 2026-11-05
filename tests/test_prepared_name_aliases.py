"""Names in the source survive inflection and transliteration; invention does not."""
import pytest

from solo.prepared_world import name_in_quote


@pytest.mark.parametrize('name,quote', [
    ('Даргерфорд', 'Дорога ведёт в Даргерфорд, где ждёт проводник.'),
    ('Даргерфорд', 'Город Daggerford стоит на реке.'),
    ('Дралморрер', 'Отряд Дралморрера замечен у моста.'),
    ('Гринест', 'Гринест горит, и дым виден с холма.'),
])
def test_verified_names_survive_layout_and_spelling(name, quote):
    assert name_in_quote(name, quote)


@pytest.mark.parametrize('name,quote', [
    # A different name that merely shares a prefix must not ride along.
    ('Джалморрер', 'Отряд Дралморрера замечен у моста.'),
    ('Линан', 'Скорее в крепость, там безопасно.'),
    # Short names are not allowed to fuzzy-match: one letter is decisive.
    ('Кат', 'Кот перебежал улицу.'),
])
def test_unrelated_names_do_not_match(name, quote):
    assert not name_in_quote(name, quote)


def test_soft_line_breaks_inside_a_quote_do_not_break_matching():
    """The extractor inserts U+FFFE; a real citation must still resolve."""
    assert name_in_quote('Даргерфорд', 'Путь на\ufffeДаргерфорд\ufffeуказан.')
