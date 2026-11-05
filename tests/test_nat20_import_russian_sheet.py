"""A sheet written in Russian is the normal case, not the exception.

The whole product speaks Russian, so a player pastes "Класс: волшебник" and
"Вид: высокий эльф". The corpus names classes and species in English only, so
the reader carries those words itself. The earlier tests missed this because
they paired Russian labels with English values.
"""
from solo.nat20_import import parse_sheet

SHEET = (
    "Имя: Лира\n"
    "Класс: волшебник\n"
    "Уровень: 3\n"
    "Вид: высокий эльф\n"
    "Предыстория: мудрец\n"
    "Сила 8\nЛовкость 14\nТелосложение 13\n"
    "Интеллект 16\nМудрость 12\nХаризма 10\n"
)


def test_a_sheet_written_in_russian_is_read_without_guessing():
    read = parse_sheet(SHEET)
    draft = read["draft"]
    assert draft["class_slug"] == "wizard"
    assert draft["species_slug"] == "elf-high"
    assert draft["background_slug"] == "sage"
    assert draft["level"] == 3
    assert read["ready"] is True
    assert read["missing"] == []
    scores = {name: (row["value"] if isinstance(row, dict) else row)
              for name, row in draft["ability_scores"].items()}
    assert scores == dict(strength=8, dexterity=14, constitution=13,
                          intelligence=16, wisdom=12, charisma=10)


def test_the_russian_words_keep_the_english_reading_working():
    read = parse_sheet("Class: fighter\nSpecies: dwarf\n")
    assert read["draft"]["class_slug"] == "fighter"
    assert read["draft"]["species_slug"] == "dwarf"
