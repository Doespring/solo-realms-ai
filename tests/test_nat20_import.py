"""Local-first pasted-sheet import: no model, no silent fixes."""
import os
os.environ['SOLO_TEST']='1'

import pytest
from pydantic import ValidationError

from solo.nat20_import import parse_sheet

SHEET = """Арден из Крепости
Класс: Fighter
Вид: Human
Предыстория: Soldier
Уровень: 3
Сила 16, Ловкость 14, Телосложение 15
Интеллект 10, Мудрость 12, Харизма 8
"""


def test_a_readable_sheet_becomes_a_native_draft():
    result = parse_sheet(SHEET)
    draft = result["draft"]
    assert draft["species_slug"] == "human"
    assert draft["class_slug"] == "fighter"
    assert draft["background_slug"] == "soldier"
    assert draft["level"] == 3
    assert draft["ability_scores"] == {
        "strength": 16, "dexterity": 14, "constitution": 15,
        "intelligence": 10, "wisdom": 12, "charisma": 8,
    }
    assert draft["name"] == "Арден из Крепости"
    assert result["missing"] == []


def test_every_taken_fact_names_the_line_it_came_from():
    result = parse_sheet(SHEET)
    fields = {row["field"]: row for row in result["fields"]}
    assert fields["species"]["line"] == "Вид: Human"
    assert fields["class"]["line"] == "Класс: Fighter"
    assert fields["ability.strength"]["value"] == 16
    assert "Сила 16" in fields["ability.strength"]["line"]


def test_the_original_text_is_returned_unchanged():
    text = SHEET + "\nЗаметка: игрок дописал строку.\n"
    assert parse_sheet(text)["source"] == text


def test_an_ambiguous_name_is_offered_rather_than_guessed():
    result = parse_sheet("Класс: Fighter\nВид: Elf\n")
    species = [row for row in result["ambiguous"] if row["field"] == "species"]
    assert species and species[0]["alternatives"]
    assert any(slug.startswith("elf") for slug in species[0]["alternatives"])
    assert result["ready"] is False


def test_an_out_of_range_level_is_reported_not_clamped():
    result = parse_sheet("Класс: Fighter\nВид: Human\nУровень: 30\n")
    assert result["draft"]["level"] == 1
    assert any(row["field"] == "level" for row in result["unknown"])
    assert all(row["field"] != "level" for row in result["fields"])


def test_a_partial_sheet_still_parses_and_says_what_is_missing():
    result = parse_sheet("Дневник героя без названий.")
    assert result["missing"] == ["species_slug", "class_slug"]
    assert result["ready"] is False


def test_the_draft_is_a_real_wizard_draft():
    from solo.nat20_wizard import WizardDraft

    draft = parse_sheet(SHEET)["draft"]
    assert WizardDraft.model_validate(draft).class_slug == "fighter"


def test_no_external_client_is_used(monkeypatch):
    """The parser cannot reach a model even by accident."""

    def forbidden(*args, **kwargs):
        raise AssertionError("import must stay local")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    assert parse_sheet(SHEET)["draft"]["class_slug"] == "fighter"
