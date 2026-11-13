"""The control book must play: walk, choose, decline a fight, be paid.

The compiler test proves the world is derived from the printed pages. This test
proves the derived world is a game: the party enters, a printed road is usable,
a named opponent is an offer rather than a forced battle, declining it costs no
blood, and the printed reward is paid once into the pool the table shares.
"""
import json
import sys
from pathlib import Path

import pytest

from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import run
from solo.contracts import Hero
from solo.llm import MockLLM
from solo.pdf_pipeline import PDFPipeline
from solo.prepared_world import PreparedWorldValidator
from solo.progression import Progression, payout
from solo.storage import Store
from solo.world import ScenarioPackage, WorldSettings, locations_view, project
from solo.world_service import WorldCommand, WorldImport, WorldService

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures.control_book import build as build_control_book  # noqa: E402


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp('control-play')
    path = root / 'lantern-quarry.pdf'
    build_control_book(path)
    library = AdventureLibrary(root)
    bundle = library.create('The Lantern Quarry')['id']
    job = library.enqueue(bundle, path, 'lantern-quarry.pdf', 'book')
    run(library.data, job['id'])
    document = library.data['documents'][0]['id'] if False else job['document']
    PDFPipeline(library).index(document)
    with library.db() as db:
        pages = {row['number']: row['content'] for row in
                 db.execute('SELECT number,content FROM pages WHERE document=?', (document,))}
    def quote(number, anchor, name):
        text = pages[number]
        start = text.find(anchor)
        assert start >= 0, (number, anchor)
        chunk = text[start:]
        assert name.split()[-1].lower() in chunk.lower()
        return dict(document=document, page=number, quote=chunk)
    entities = [
        dict(key='bell-landing', kind='location', evidence=[quote(1, 'The Bell Landing', 'Bell Landing')],
             data=dict(name='Bell Landing', description='Fog sits on the water and the bell rope knocks against its post.')),
        dict(key='sunken-gallery', kind='location', evidence=[quote(3, 'The Sunken Gallery', 'Sunken Gallery')],
             data=dict(name='The Sunken Gallery', description='Water covers the floor to the ankle.')),
        dict(key='gallery-kobolds', kind='opponent', evidence=[quote(3, 'The Sunken Gallery', 'Sunken Gallery')],
             data=dict(name='kobolds', count=3,
                       condition='The gallery can be entered without a fight if the bell has been rung three times.')),
    ]
    result = PreparedWorldValidator(library, [document]).compile(
        [dict(title='The Lantern Quarry', start='Bell Landing', ruleset='dnd2014', entities=entities)],
        'dnd2024', module='control', source_ruleset='dnd2014')
    assert result['ready'], result['quarantine']
    return result


def table(tmp_path, world, *, mode='sandbox', travel=True):
    """A campaign that opens where the book opens, with its prepared places."""
    package = ScenarioPackage.model_validate(world['definition']['world'])
    start = world['start']
    # The party begins at the book's own opening place, not in a demo tavern: the
    # launch path installs the prepared world the same way, and importing it on top
    # of an unrelated scene would leave the hero standing on a road that is not his.
    opening = dict(location=start, turn=0, facts=[], deferred=[], locations=[],
                   world=dict(settings=WorldSettings(mode=mode, map_travel=travel).model_dump()))
    store = Store(tmp_path / 'play.sqlite3')
    cid = store.create('Контроль', Hero(name='Рив'), initial_state=opening)['id']
    service = WorldService(store)
    service.import_world(cid, WorldImport(request_id='import', confirm=True, package=package))
    return store, cid, service


def test_a_named_road_makes_the_far_end_known_but_never_reads_it_aloud(world, tmp_path):
    package = world['definition']['world']
    assert package['routes'], 'the book prints a way on'
    nodes = {node['name']: node for node in package['nodes']}
    # Hearing of a place is not visiting it: the printed road names where it leads.
    assert nodes['The Sunken Gallery']['known']
    store, cid, service = table(tmp_path, world)
    printed = [place['description'] for place in package['locations']
               if place['name'] == 'The Sunken Gallery'][0]
    assert printed
    map_view = json.dumps(project(store.campaign(cid)['state']), ensure_ascii=False)
    assert 'The Sunken Gallery' in map_view, 'the road end is named to the party'
    # What the party has instead is a neutral notice, not the page's own words.
    seen = json.dumps(locations_view(store.campaign(cid)['state']), ensure_ascii=False)
    assert printed not in seen, 'its printed prose stays private until visited'
    stub = store.campaign(cid)['state']['world']['observations']['The Sunken Gallery']
    assert stub['description'] == 'Описание станет доступно после посещения.'
    walked = service.execute(cid, WorldCommand(request_id='walk', kind='travel',
                                               target='The Sunken Gallery'), from_map=True)
    assert store.campaign(cid)['state']['location'] == 'The Sunken Gallery'
    assert walked
    at_end = json.dumps(locations_view(store.campaign(cid)['state']), ensure_ascii=False)
    assert printed in at_end, 'standing there reads the page aloud'


def test_a_monster_mention_is_only_an_offer_and_declining_costs_nothing(world, tmp_path):
    """A named monster is a fact about the place, not an order to roll initiative."""
    store, cid, service = table(tmp_path, world)
    service.execute(cid, WorldCommand(request_id='walk', kind='travel',
                                      target='The Sunken Gallery'), from_map=True)
    before = store.campaign(cid)['state']
    offer = before['world'].get('pending_encounter') or {}
    assert offer.get('id'), 'the kobolds are offered where the page puts them'
    assert not before.get('combat'), 'being named is not being attacked'
    service.execute(cid, WorldCommand(request_id='decline', kind='decline_encounter'))
    after = store.campaign(cid)['state']
    assert not (after['world'].get('pending_encounter') or {}).get('id')
    assert not after.get('combat')
    assert after['hero']['hp'] == before['hero']['hp']
    # The printed way past the fight survives as a condition of the place.
    gallery = next(place for place in after['locations'] if place['name'] == 'The Sunken Gallery')
    assert any('without a fight' in str(item) for item in gallery.get('secrets') or [])


def test_a_printed_reward_is_paid_once_into_a_pool_the_party_shares(world):
    progression = Progression.model_validate(world['definition']['progression'])
    assert progression.mode == 'experience'
    rule = progression.rewards[0]
    # The same printed line pays the same pool whether one hero or four sit at the table.
    assert payout(rule, party_size=1) == payout(rule, party_size=4) == rule.amount

def test_a_saved_world_survives_a_restart_and_continues(world, tmp_path):
    """Closing the app is not losing the table: the party resumes where it stood."""
    store, cid, service = table(tmp_path, world)
    service.execute(cid, WorldCommand(request_id='walk', kind='travel',
                                      target='The Sunken Gallery'), from_map=True)
    saved = store.campaign(cid)['state']
    assert saved['location'] == 'The Sunken Gallery'
    minutes = saved.get('elapsed_minutes', 0)
    # A second process opens the same database: nothing is replayed, nothing is lost.
    reopened = Store(tmp_path / 'play.sqlite3')
    resumed = reopened.campaign(cid)['state']
    assert resumed['location'] == saved['location']
    assert resumed.get('elapsed_minutes', 0) == minutes
    assert {p['name'] for p in resumed['locations']} == {p['name'] for p in saved['locations']}
    # And play continues from there rather than from the opening scene.
    continued = WorldService(reopened)
    continued.execute(cid, WorldCommand(request_id='back', kind='travel',
                                        target='Bell Landing'), from_map=True)
    assert reopened.campaign(cid)['state']['location'] == 'Bell Landing'

def test_free_text_walking_uses_the_world_layer_and_asks_no_model(world, tmp_path):
    """Typing the way to a known place is a move, not a request for narration."""
    import asyncio
    from solo.game import Game
    store, cid, service = table(tmp_path, world)
    llm = MockLLM()
    game = Game(store, llm)
    version = store.campaign(cid)['version']
    answer = asyncio.run(game.play_command(cid, 'walk-text', 'Иду в The Sunken Gallery', version))
    assert answer['status'] == 'resolved', answer
    assert store.campaign(cid)['state']['location'] == 'The Sunken Gallery'
    assert not llm.metrics, 'the road is known; nobody had to be asked where it goes'
