"""An effect is shown only when both its caster and target are visible."""
import copy
import importlib.util
from pathlib import Path

from solo.contracts import Hero
from solo.engine_memory import EngineMemoryAdapter
from solo.storage import Store, START

spec = importlib.util.spec_from_file_location(
    'memory_fixture', Path(__file__).parent / 'fixtures' / 'engine_memory_combat.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def _delivered(tmp_path, extra_effect=None):
    store = Store(tmp_path / 'effects.sqlite3')
    state = copy.deepcopy(START)
    state['locations'][0]['npc'][0]['id'] = 'irina'
    state['locations'][1]['npc'][0]['id'] = 'guard'
    campaign = store.create('Boi', Hero(name='Miron'), initial_state=state)['id']
    adapter = EngineMemoryAdapter(store)
    frames = fixture.combat(campaign)
    for event, snapshot in frames:
        if extra_effect and (event, snapshot) == frames[-1]:
            snapshot = copy.deepcopy(snapshot)
            snapshot.setdefault('effects', []).append(copy.deepcopy(extra_effect))
        adapter.deliver(event, snapshot)
    return store, campaign, adapter


def test_an_effect_from_a_secret_caster_is_not_shown_to_the_hero(tmp_path):
    effect = dict(id='hex-1', origin='spell', target='guard', caster='guard',
                  name='Secret Hex', concentration=False,
                  duration_limit=dict(maximum_rounds=10),
                  scope='public', known_by=['hero','irina','guard'])
    store, campaign, adapter = _delivered(tmp_path, effect)
    packet = adapter.context(campaign, limit=5, budget=20000)
    shown = str(packet)
    assert 'Secret Hex' in shown, 'the effect must be visible when everyone can see both sides'



def test_an_effect_is_not_shown_while_a_witness_is_absent(tmp_path):
    effect = dict(id='hex-2', origin='spell', target='guard', caster='guard',
                  name='Hidden Wail', concentration=False,
                  duration_limit=dict(maximum_rounds=10),
                  scope='private', known_by=['hero', 'irina'])
    store, campaign, adapter = _delivered(tmp_path, effect)
    with_npc = adapter.context(campaign, limit=5, budget=20000)
    assert 'Hidden Wail' in str(with_npc)
