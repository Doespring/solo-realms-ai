"""Where the time of one turn goes, measured instead of guessed.

Section 9.2 asked for the operation to be split into phases: read, queue,
interpret, compute, commit, narrate. The audit measured 1.58 s for a single
start_combat; a phase split says which part to attack, and shows that only the
model wait runs outside the campaign lock.

These tests pin the split itself, not a fixed duration: a machine that is busy
must not make the contract fail.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio
from types import SimpleNamespace

from solo.contracts import Hero
from solo.game import Game
from solo.llm import MockLLM
from solo.storage import Store

# 'осматриваюсь' and other common phrases are answered before the model is
# asked at all, so the split stays at zero for them and proves nothing: a test
# built on one looked green while measuring nothing. This sentence genuinely
# needs the interpreter.
SENTENCE = 'внимательно изучаю грязь у двери'


from solo.contracts import Plan, Step


class SlowModel(MockLLM):
    """A model that answers slowly, on the path the player actually uses.

    MockLLM raises without a scripted plan, and the player's path short-circuits
    simple sentences before reaching the model at all - so the plan is fixed and
    the delay is put on interpret(), which is what Game.play awaits.
    """
    delay = 0.4
    plan = Plan(summary='Осматриваю следы', steps=[Step(kind='check', text='Осматриваю следы', ability='wisdom', dc=12)])

    async def interpret_command(self, *a, **k):
        await asyncio.sleep(self.delay)
        return await super().interpret_command(*a, **k)

    async def interpret(self, *a, **k):
        await asyncio.sleep(self.delay)
        return await super().interpret(*a, **k)


def _game(tmp_path, llm=None):
    store = Store(tmp_path/'phases.sqlite3')
    if llm is None:
        llm = SlowModel()
        llm.plan = SlowModel.plan
    elif isinstance(llm, MockLLM) and not llm.plan:
        # MockLLM raises ValueError without a scripted plan, which would look
        # like a "fast model" and hide whether the wait was measured at all.
        llm.plan = SlowModel.plan
    game = Game(store, llm)
    cid = store.create('К', Hero(name='Мирон', hp=12, max_hp=12))['id']
    return game, store, cid


def _play(game, store, cid, request_id, text):
    """The player's own path: POST /play calls Game.play, not play_command."""
    request = SimpleNamespace(request_id=request_id, text=text, selection=None)
    return asyncio.run(game.play(cid, request))


def test_every_turn_reports_its_phases(tmp_path):
    game, store, cid = _game(tmp_path)
    result = _play(game, store, cid, 'phases-1', SENTENCE)
    timings = result.get('timings')
    assert timings, 'a turn must report where its time went'
    # The audit asked for read, queue, interpretation, calculation, commit and
    # narration by name; 'received' is the wait for the campaign lock (the queue).
    for phase in ('received', 'read', 'model_wait', 'engine', 'commit', 'narrate'):
        assert phase in timings, f'{phase} missing from {sorted(timings)}'
        assert timings[phase] >= 0


def test_the_model_wait_is_measured_as_the_dominant_phase(tmp_path):
    game, store, cid = _game(tmp_path)
    timings = _play(game, store, cid, 'phases-2', SENTENCE)['timings']
    assert timings['model_wait'] >= 0.3, f"model wait not measured: {timings}"
    assert timings['model_wait'] > timings['engine'], f"engine cost hidden in the model wait: {timings}"


def test_a_fast_model_needs_no_model_wait(tmp_path):
    """The split must not invent a wait that did not happen."""
    game, store, cid = _game(tmp_path, MockLLM())
    timings = _play(game, store, cid, 'phases-3', SENTENCE)['timings']
    assert timings['model_wait'] < 0.15, f"a fast model reported a slow wait: {timings}"


def test_the_lock_is_not_held_during_the_model_wait(tmp_path):
    """Proof in the mechanism, not only in the numbers."""
    game, store, cid = _game(tmp_path)

    async def run():
        task = asyncio.current_task()
        seen = {'locked_during_wait': None}

        original = game.llm.interpret

        async def probe(*a, **k):
            # Called while the model is "thinking": if the lock were held here,
            # no other request could read state until the answer arrived.
            seen['locked_during_wait'] = game.operation.locked()
            return await original(*a, **k)

        game.llm.interpret = probe
        await game.play(cid, SimpleNamespace(request_id='phases-4', text=SENTENCE, selection=None))
        return seen, task

    seen, _ = asyncio.run(run())
    assert seen['locked_during_wait'] is False, 'the campaign lock was held across the model wait'
