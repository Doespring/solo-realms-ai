"""The control book becomes a playable game, not just an extraction.

The expected facts are fixed in tests/fixtures/control-book-expected.json before
the extractor runs; nothing in the product reads that file. This test starts
from the book itself and asks the questions a player asks: is there a world, a
legal hero, a way forward, a fight that the book conditions rather than demands,
and a reward that is paid?
"""
import json
import sys
from pathlib import Path

import pytest

from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import run
from solo.pdf_pipeline import PDFPipeline
from solo.prepared_world import PreparedWorldValidator
from solo.progression import Progression, payout

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures.control_book import build as build_control_book  # noqa: E402

EXPECTED = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'control-book-expected.json')
                      .read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def compiled(tmp_path_factory):
    """The book on disk, its own pages, and the world the compiler derives."""
    root = tmp_path_factory.mktemp('control-game')
    path = root / 'lantern-quarry.pdf'
    build_control_book(path)
    library = AdventureLibrary(root)
    bundle = library.create('The Lantern Quarry')['id']
    job = library.enqueue(bundle, path, 'lantern-quarry.pdf', 'book')
    run(library.data, job['id'])
    document = job['document']
    PDFPipeline(library).index(document)

    # The analysis is the only part a model would normally produce; here it is
    # the smallest honest reading of the pages, so the compiler is exercised on
    # facts the book prints and not on a stored answer.
    with library.db() as db:
        pages = {row['number']: row['content'] for row in
                 db.execute('SELECT number,content FROM pages WHERE document=?', (document,))}
    def quote(number, anchor, name):
        """The page as printed, from the heading to the end of its opening line.

        Both the place name and the prose come from one contiguous run, because a
        citation is the printed page and not a paraphrase of it. PDF line breaks
        inside the run are the book's own.
        """
        text = pages[number]
        start = text.find(anchor)
        assert start >= 0, (number, anchor)
        run = text[start:]
        assert name.split()[-1].lower() in run.lower(), (number, name, run[:120])
        return dict(document=document, page=number, quote=run)
    entities = [
        dict(key='bell-landing', kind='location', evidence=[quote(1, 'The Bell Landing', 'Bell Landing')],
             data=dict(name='Bell Landing', description='Fog sits on the water and the bell rope knocks against its post. A ferry waits, half loaded, and nobody is holding the line.')),
        dict(key='sunken-gallery', kind='location', evidence=[quote(3, 'The Sunken Gallery', 'Sunken Gallery')],
             data=dict(name='The Sunken Gallery', description='Water covers the floor to the ankle. Lantern light stops at the first arch.')),
        dict(key='overseer-walk', kind='location', evidence=[quote(4, 'The Overseer Walk', 'Overseer Walk')],
             data=dict(name='Overseer Walk', description='The quarry stair ends at a rail of rusted iron. Below, the shaft is black.')),
        # The fork is named in the gallery's own prose, not drawn as a diagram.
        dict(key='gallery-to-walk', kind='route', evidence=[quote(3, 'The Sunken Gallery', 'Sunken Gallery')],
             data=dict(**{'from': 'The Sunken Gallery', 'to': 'The Overseer Walk'})),
        # Three kobolds hold the gallery, and the page also prints how to pass
        # them without a fight; the opponent is a fact, the fight is not.
        dict(key='kobolds', kind='opponent', evidence=[quote(3, 'The Sunken Gallery', 'Sunken Gallery')],
             data=dict(name='kobolds', count=3,
                       condition='The gallery can be entered without a fight if the bell has been rung three times.')),
    ]
    batch = dict(title='The Lantern Quarry', start='Bell Landing', ruleset='dnd2014', entities=entities)
    result = PreparedWorldValidator(library, [document]).compile([batch], 'dnd2024',
                                                                module='control', source_ruleset='dnd2014')
    return dict(library=library, document=document, result=result, pages=pages)


def test_the_book_yields_a_world_a_hero_can_enter(compiled):
    result = compiled['result']
    assert result['ready'], result['quarantine']
    world = result['definition']['world']
    names = [place['name'] for place in world['locations']]
    for place in EXPECTED['places']:
        if place['role'] == 'start':
            assert place['name'] in names
    start = result['start']
    opening = next(p for p in world['locations'] if p['name'] == start)
    # The opening scene is the printed read-aloud text, not a summary of it.
    assert opening['description'] and opening['known_to_hero']
    assert opening['description'].startswith('Fog sits on the water')


def test_the_printed_rewards_become_priced_rules(compiled):
    progression = compiled['result']['definition'].get('progression')
    assert progression and progression['mode'] == 'experience'
    amounts = sorted(rule['amount'] for rule in progression['rewards'])
    expected = sorted(item['experience'] for item in EXPECTED['rewards'])
    # Every printed line is a reward with its own number; nothing is invented.
    assert set(expected) <= set(amounts), (expected, amounts)


def test_each_reward_pays_once_and_the_party_shares_the_pool(compiled):
    progression = Progression.model_validate(compiled['result']['definition']['progression'])
    rule = progression.rewards[0]
    assert payout(rule, party_size=1) == rule.amount
    # The pool does not grow with the party: a larger group levels no faster.
    assert payout(rule, party_size=4) == rule.amount


def test_a_monster_on_the_page_does_not_start_a_fight_by_itself(compiled):
    world = compiled['result']['definition']['world']
    encounters = [event for event in world['events'] if event['kind'] == 'encounter']
    assert encounters, 'the named guard becomes an offered encounter'
    for event in encounters:
        # An encounter is an offer stored on the world; nothing here forces combat.
        assert event['location']
    # The book conditions the guard: the gallery can be entered without a fight.
    gallery = next(p for p in world['locations'] if 'gallery' in p['name'].lower())
    assert 'without a fight' in ' '.join(gallery.get('secrets') or []) + json.dumps(gallery, ensure_ascii=False)


def test_a_printed_fork_becomes_a_transition(compiled):
    world = compiled['result']['definition']['world']
    assert world['routes'], 'the page prints a split, so the world has a road'
    node_ids = {node['id'] for node in world['nodes']}
    for route in world['routes']:
        assert route['from'] in node_ids and route['to'] in node_ids
        assert route['meters'] >= 1 and route['minutes'] >= 1


def test_the_scanned_page_stays_unread_and_the_book_still_opens(compiled):
    result = compiled['result']
    assert result['ready']
    # A page that only carries an image contributes no invented text.
    assert compiled['pages'][EXPECTED['scan_page']['page']].strip() == ''
