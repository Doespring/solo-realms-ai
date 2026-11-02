"""The class gate comes from reviewed data, never from a wildcard."""
import json
from pathlib import Path

from solo.nat20_wizard import CLASS_SPELL_LISTS, _spell_pool, class_spell_lists


def test_every_srd_class_has_a_reviewed_spell_list():
    lists = class_spell_lists()
    assert sorted(lists) == ["bard", "cleric", "druid", "paladin", "ranger",
                             "sorcerer", "warlock", "wizard"]
    # A spell belongs to the classes its printed list names, not to all of them.
    assert "fireball" in lists["wizard"] and "fireball" not in lists["cleric"]
    assert "cure-wounds" in lists["cleric"] and "cure-wounds" not in lists["wizard"]


def test_the_export_cites_its_source_and_is_not_a_wildcard():
    payload = json.loads(Path(CLASS_SPELL_LISTS).read_text(encoding="utf-8"))
    assert payload["license"] == "CC-BY-4.0"
    assert "content24" in payload["source"]
    # No class may claim every spell: that would be the "everyone casts
    # everything" rule the prompt forbids as a substitute for legality.
    total = payload["_stats"]["spells"]
    for klass, spells in payload["classes"].items():
        assert 0 < len(spells) < total, klass


def test_a_class_pool_never_offers_another_classes_spell():
    from nat20_builds import loader as corpus_loader
    loader = corpus_loader()
    wizard = {row["id"] for row in _spell_pool(loader, cantrips=False, ceiling=9, klass="wizard")}
    cleric = {row["id"] for row in _spell_pool(loader, cantrips=False, ceiling=9, klass="cleric")}
    assert "fireball" in wizard and "fireball" not in cleric
    assert "cure-wounds" in cleric and "cure-wounds" not in wizard

