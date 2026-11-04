"""The reviewed episode-one closure stays tied to the printed pages."""
import pytest

from solo import first_episode as ep


def test_closure_is_pinned_to_one_document_and_its_pages():
    assert set(ep.ANCHORS) == {10, 11, 13, 14, 15}
    assert ep.MODULE == 'hoard-episode-1'


def test_flatten_rejoins_words_split_by_the_extractor():
    """U+FFFE sits at a hyphenation point, so removing it restores the word."""
    assert ep.flatten('попытать\ufffeся') == 'попытаться'
    assert ep.flatten('пе\u00adре\u00adулок') == 'переулок'
    assert ep.flatten('  Город\n\tгорит  ') == 'Город горит'


def test_the_mandatory_encounter_uses_the_printed_warband():
    """Page 10 fixes eight kobolds; the closure must not invent a difficulty."""
    encounter = ep.encounter()
    assert len(encounter['actors']) == ep.KOBOLDS == 8
    assert {actor['monster'] for actor in encounter['actors']} == {ep.MONSTER}
    cells = {(a['position']['x'], a['position']['y']) for a in encounter['actors']}
    assert len(cells) == 8, 'actors may not share a starting cell'
    assert len(ep.encounters()) == 1 and ep.FIRST_ENCOUNTER in ep.encounters()


def test_verification_refuses_a_page_that_lost_its_quotes():
    """A different or revised document must not silently reuse this closure."""

    class Missing:
        def db(self):
            raise AssertionError('the anchor check must read the page')

    with pytest.raises(Exception):
        ep.verify_anchors(Missing())
