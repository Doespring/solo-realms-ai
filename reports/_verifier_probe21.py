
import os, sys, asyncio, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero, Step, Plan
from solo.llm import MockLLM

class Slow(MockLLM):
    async def interpret(self, text, scene):
        await asyncio.sleep(0.6)
        return await super().interpret(text, scene)

async def one(label, c1_waits, c2_runs_before, rounds=4):
    hits = 0
    for r in range(rounds):
        tmp = Path(tempfile.mkdtemp())
        plan = Plan(summary='О', steps=[Step(kind='observe', text='Осматриваю', disposition='now')], needs_confirmation=False)
        app = create_app(tmp/('w%d.sqlite3' % r), Slow(plan))
        store = app.state.store; game = app.state.game
        c1 = store.create('K1', Hero(name='A', hp=12, max_hp=12))['id']
        c2 = store.create('K2', Hero(name='B', hp=12, max_hp=12))['id']
        marks = []
        orig_route = game.route
        async def route(campaign, request, _o=orig_route):
            # tag every acquisition window by wrapping the lock use of route
            marks.append(('enter', campaign[:4]))
            try: return await _o(campaign, request)
            finally: marks.append(('leave', campaign[:4]))
        game.route = route
        tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=30) as api:
            await asyncio.sleep(0.1)
            body = lambda rid, txt: {'request_id': rid, 'text': txt, 'mode': 'action',
                                     'step': Step(kind='observe', text='Осматриваю', disposition='now').model_dump()}
            if c1_waits:
                slow = asyncio.create_task(api.post('/api/campaigns/%s/actions' % c1, json=body('s1','прислушиваюсь к тишине за дверью')))
                await asyncio.sleep(0.15)
                other = asyncio.create_task(api.post('/api/campaigns/%s/actions' % c2, json=body('o1','осматриваюсь')))
                await asyncio.gather(slow, other)
            # order matters: which of the two tasks is *already waiting* on the
            # lock when the survivor re-acquires it?
            t_a = asyncio.create_task(api.post('/api/campaigns/%s/actions' % c1, json=body('a1','осматриваюсь')))
            await asyncio.sleep(c2_runs_before)
            t_b = asyncio.create_task(api.post('/api/campaigns/%s/actions' % c2, json=body('b1','осматриваюсь')))
            await asyncio.gather(t_a, t_b)
            game.route = orig_route
        # overlap of route bodies is expected only if something released by mistake;
        # measure directly on the lock instead
        hits += 1
    return hits

async def direct():
    """The precise question: a task that entered through operation_without while a
    second task was already waiting -> who takes the lock next, and is the
    survivor still the exclusive owner?"""
    tmp = Path(tempfile.mkdtemp())
    app = create_app(tmp/'x.sqlite3', MockLLM())
    game = app.state.game
    trace = []

    async def survivor():
        async with game.operation:
            game._operation_owner = asyncio.current_task()
            tr = asyncio.current_task()
            trace.append(('A-holds', tr.get_name()))
            async with game.operation_without('c'):
                await asyncio.sleep(0.05)              # B queues here
                await asyncio.sleep(0.25)              # and gets the lock, then runs
            trace.append(('A-reacquired', tr.get_name(), game._operation_owner is tr))
            await asyncio.sleep(0.2)

    async def bystander():
        await asyncio.sleep(0.02)
        async with game.operation:
            tr = asyncio.current_task()
            trace.append(('B-holds', tr.get_name()))
            trace.append(('B-owner-field-overwritten', game._operation_owner is tr))
            await asyncio.sleep(0.15)

    await asyncio.gather(survivor(), bystander())
    for t in trace: print("   ", t)
    print("   final owner field attr None?", game._operation_owner is None, "locked:", game.operation.locked())

asyncio.run(direct())
