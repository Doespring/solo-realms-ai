"""The reviewed starting-kit table must match the pinned corpus prose.

``solo/nat20_kit.py`` is a transcription of the SRD 5.2 starting-equipment
sentences, and the corpus is the only authority for what those kits contain.
These tests re-read the corpus and fail the moment the transcription drifts: a
kit letter that moved, an item the prose names and the table lost, an item slug
the corpus does not publish, or a gold amount that no longer matches the award.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nat20_builds import loader  # noqa: E402
from solo.nat20_kit import (  # noqa: E402
    ITEM_BY_NAME,
    KITS,
    equipment_slugs,
    grant,
    kit,
    kits,
    normalize,
    prose_line,
)


@pytest.fixture(scope="module")
def corpus():
    return loader()


def sentence(kind, source, corpus):
    doc = corpus.get_class(source) if kind == "class" else corpus.get_background(source)
    return prose_line(kind, source, str(doc.description)), doc


def test_every_document_with_a_kit_is_transcribed(corpus):
    with_kits = {("class", slug) for slug in corpus.list_slugs("classes")
                 if "Starting Equipment" in str(corpus.get_class(slug).description)}
    with_kits |= {("background", slug) for slug in corpus.list_slugs("backgrounds")
                 if "Equipment:" in str(corpus.get_background(slug).description)}
    assert with_kits == set(KITS), sorted(with_kits ^ set(KITS))


def test_the_kits_name_every_letter_the_prose_offers(corpus):
    for (kind, source) in sorted(KITS):
        line, _ = sentence(kind, source, corpus)
        letters = re.findall(r"\(([A-Z])\)", line)
        assert letters == [row["key"] for row in kits(kind, source)], (kind, source, line)


def test_every_transcribed_item_appears_in_the_prose(corpus):
    known = set(ITEM_BY_NAME)
    for (kind, source) in sorted(KITS):
        line, _ = sentence(kind, source, corpus)
        text = normalize(re.sub(r"\[\[/award \d+[Gg][Pp]\]\]", "", line))
        for row in kits(kind, source):
            for item in row["items"]:
                names = [name for name, slug in ITEM_BY_NAME.items() if slug == item["slug"]]
                assert names, f"{kind} {source}: {item['slug']} has no prose name"
                assert any(normalize(name) in text for name in names), (
                    kind, source, row["key"], item["slug"], text
                )
            for group in row["picks"]:
                offered = [name for name, slug in ITEM_BY_NAME.items()
                           if slug in {option["slug"] for option in group["options"]}]
                assert offered or not group["options"]
                assert set(offered) <= known


def test_every_item_slug_is_a_real_corpus_document(corpus):
    published = set(corpus.list_slugs("items"))
    used = {item["slug"] for (kind, source) in KITS for row in kits(kind, source)
            for item in row["items"]}
    used |= {option["slug"] for (kind, source) in KITS for row in kits(kind, source)
             for group in row["picks"] for option in group["options"]}
    assert used <= published, sorted(used - published)


def test_gold_matches_the_award_in_the_prose(corpus):
    for (kind, source) in sorted(KITS):
        line, doc = sentence(kind, source, corpus)
        # A class sentence wraps every purse in [[/award 4GP]] markup; a
        # background sentence writes the same purse as a plain "8 GP".
        awards = [int(value) for value in re.findall(r"(\d+)\s*[Gg][Pp]\b", line)]
        golds = [row["gold_gp"] for row in kits(kind, source)]
        assert golds == awards, (kind, source, line)
        assert max(golds) == int(str(doc.wealth)), (kind, source, doc.wealth)


def test_a_gold_only_kit_grants_no_items(corpus):
    for (kind, source) in sorted(KITS):
        rows = kits(kind, source)
        last = rows[-1]
        assert last["gold_gp"] == max(row["gold_gp"] for row in rows)
        granted = grant(kind, source, last["key"])
        assert granted["items"] == []
        assert granted["gold_gp"] == int(str(("class" == kind and corpus.get_class(source) or corpus.get_background(source)).wealth))


def test_a_kit_grant_resolves_variants_and_keeps_counts(corpus):
    row = grant("class", "wizard", "A", {"arcane-focus": "orb"})
    assert {"slug": "orb", "count": 1} in row["items"]
    assert {"slug": "dagger", "count": 2} in row["items"]
    assert row["gold_gp"] == 5
    assert [group["chosen"] for group in row["picks"]] == ["orb"]
    assert row["unresolved"] and "Книга заклинаний" in row["unresolved"][0]["label"]


def test_a_pick_outside_the_group_is_rejected(corpus):
    with pytest.raises(ValueError, match="допустимые варианты"):
        grant("class", "wizard", "A", {"arcane-focus": "spellbook"})
    with pytest.raises(ValueError, match="неизвестная группа"):
        grant("class", "wizard", "A", {"holy-symbol": "amulet"})


def test_an_unresolved_group_takes_no_pick(corpus):
    row = grant("background", "soldier", "A")
    assert row["picks"][0]["group"] == "gaming-set"
    assert row["picks"][0]["chosen"] is None
    assert row["picks"][0]["options"] == []
    with pytest.raises(ValueError, match="не имеет вариантов"):
        grant("background", "soldier", "A", {"gaming-set": "dice"})


def test_equipment_slugs_are_distinct_and_skip_unresolved(corpus):
    grants = [grant("class", "wizard", "A"), grant("background", "acolyte", "A")]
    slugs = equipment_slugs(grants)
    assert len(slugs) == len(set(slugs))
    assert "dagger" in slugs and "book" in slugs and "calligraphers-supplies" in slugs
    assert all(isinstance(slug, str) and slug in corpus.list_slugs("items") for slug in slugs)


def test_unknown_kits_are_rejected(corpus):
    with pytest.raises(ValueError, match="нет записанного начального комплекта"):
        kits("class", "artificer")
    with pytest.raises(ValueError, match="неизвестный начальный комплект"):
        kit("class", "wizard", "Z")
