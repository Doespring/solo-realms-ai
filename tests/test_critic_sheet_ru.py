"""A Russian player's pasted sheet must be readable by the importer.

The wizard, the labels and the whole product speak Russian, but the importer's
corpus slugs are English only, so a real sheet written the way the product
writes ("Класс: воин", "Вид: человек") yields no class and no species.
Existing tests hide this because they put Russian labels in front of English
values ("Класс: Fighter"), which is not how the player writes.

Nothing here calls a model: parse_sheet is local text matching only.
"""
import os
os.environ['SOLO_TEST'] = '1'

from solo.nat20_import import parse_sheet

RUSSIAN = """Мирон
Класс: воин
Вид: эльф
Предыстория: солдат
Уровень: 3
"""


def test_a_russian_language_sheet_is_read():
    result = parse_sheet(RUSSIAN)
    draft = result['draft']
    assert draft['class_slug'] == 'fighter', draft['class_slug']
    # The corpus knows three elf lines; an ambiguity must be offered, not guessed.
    assert draft['species_slug'] is None or draft['species_slug'].startswith('elf')
    assert any(row['field'] == 'species' for row in result['ambiguous']), result['ambiguous']
    assert draft['background_slug'] == 'soldier', draft['background_slug']
