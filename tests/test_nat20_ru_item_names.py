"""The Russian item names shipped with the handoff stay in step with the corpus.

The canonical corpus is upstream data and is left untouched; the translation
is a separate resource the host merges at display time. That split is only safe
if the two never drift -- a renamed or added item would otherwise show its
English name, or worse, an old translation of a different item.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dnd5e_engine.lib_loader import get_lib_loader

TRANSLATIONS = Path(__file__).resolve().parents[1] / "integration/engine-handoff/ru_items.json"


@pytest.fixture(scope="module")
def translations():
    return json.loads(TRANSLATIONS.read_text(encoding="utf-8"))


def test_every_corpus_item_has_a_russian_name(translations):
    """A newly added item must come with a name, not fall back to English."""
    loader = get_lib_loader()
    missing = [
        slug for slug in loader.list_slugs("items") if slug not in translations
    ]
    assert not missing, f"items with no Russian name: {missing[:10]}"


def test_the_english_name_is_recorded_alongside_the_translation(translations):
    """The pair proves the translation belongs to THIS item, not a stale one."""
    loader = get_lib_loader()
    wrong = [
        slug
        for slug, entry in translations.items()
        if not slug.startswith("_")
        and entry["name"] != loader.get_item(slug).name
    ]
    assert not wrong, f"translation bound to a different item: {wrong[:10]}"


def test_no_translation_is_left_in_english(translations):
    """An untranslated copy of the English name is a silent failure."""
    unchanged = [
        slug
        for slug, entry in translations.items()
        if not slug.startswith("_") and entry["name_ru"].lower() == entry["name"].lower()
    ]
    assert not unchanged, f"items still in English: {unchanged[:10]}"


def test_the_meta_block_states_this_is_not_an_official_translation(translations):
    """The handoff must not imply Wizards has blessed this wording."""
    assert "not an official" in translations["_meta"]["kind"]