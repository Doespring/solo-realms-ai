"""Scenario 3 (one recommended level) and scenario 5 (a mixed book).

The samples are built by this file's fixture, so the check is about what the
analyzer does with a column layout, a table, a map and a scan, not about a
fixture someone else prepared.
"""
import os
os.environ['SOLO_TEST']='1'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent/'fixtures'))

import pytest
from analyzer_samples import one_level_pdf, mixed_pdf
from solo.preparation import extract
from solo.prepared_world import level_bounds, printed_heading


def test_one_recommended_level_is_read_as_a_single_level():
    pages, quality, _ = extract('salt-road.pdf', one_level_pdf())
    text='\n'.join(page for _, page in pages)
    assert 'Salt Road' in text
    assert level_bounds(text)==[(7,7)]
    # A heading printed on its own line is the book's title; text that only
    # mentions the title inside a sentence is not mistaken for a place name.
    assert printed_heading('The Salt Road\nAn adventure for characters of 7th level.')=='The Salt Road'
    assert printed_heading('The Salt Road. An adventure for characters of 7th level.')==''


def test_level_is_read_in_every_printed_form():
    assert level_bounds('An adventure for characters of 7th level.')==[(7,7)]
    assert level_bounds('7th-level characters.')==[(7,7)]
    assert level_bounds('For 3rd-5th level characters.')==[(3,5)]
    assert level_bounds('Для персонажей 5 уровня.')==[(5,5)]
    assert level_bounds('Для персонажей седьмого уровня.')==[(7,7)]
    assert level_bounds('Поговорим о погоде.')==[]


def test_mixed_book_keeps_text_table_map_and_admits_the_scan():
    pages, quality, _ = extract('lighthouse.pdf', mixed_pdf())
    assert len(pages)==5
    text='\n'.join(page for _, page in pages)
    # Ordinary prose survives.
    assert level_bounds(text)==[(4,4)]
    assert 'Keeper Alva' in text
    # The table keeps its rows and its printed amounts.
    assert 'A brass compass' in text and '15' in text
    # A map is kept as printed labels, not as verified topology.
    assert 'Lamp Room' in text
    # The scan has no text layer, and the book says so instead of guessing.
    assert pages[3][1].strip()==''
    assert quality['warnings']
    assert any('без достаточного текста' in w for w in quality['warnings'])


def test_columns_are_read_as_separate_paragraphs_not_merged_lines():
    pages, quality, _ = extract('lighthouse.pdf', mixed_pdf())
    prose=pages[4][1]
    lines=[line.strip() for line in prose.splitlines() if line.strip()]
    # Each column keeps its own sentences: no line is two columns joined.
    assert 'Harbour notes' in lines
    assert 'Lamp notes' in lines
    for line in lines:
        assert not ('signed twice' in line and 'storm' in line)


def test_every_printed_place_of_the_mixed_book_is_quotable():
    pages, quality, _ = extract('lighthouse.pdf', mixed_pdf())
    text='\n'.join(page for _, page in pages)
    for quote in ('Wet stone shines below the tower.','A lamp still burns at the top of the stairs.'):
        assert quote in text
