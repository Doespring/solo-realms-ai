
import os, sys, asyncio, tempfile, time
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero, Step, Plan
from solo.llm import MockLLM

class Slow(MockLLM):
    async def interpret(self, text, scene):
        await asyncio.sleep(0.5)
        return await super().interpret(text, scene)

async def main():
    hits = 0
    for rnd in range(5):
        tmp = Path(tempfile.mkdtemp())
        plan = Plan(summary='О', steps=[Step(kind='observe', text='Осматриваю', disposition='now')], needs_confirmation=False)
        app = create_app(tmp/('z%d.sqlite3' % rnd), Slow(plan))
        store = app.state.store; game = app.state.game
        c1 = store.create('K1', Hero(name='A', hp=12, max_hp=12))['id']
        c2 = store.create('K2', Hero(name='B', hp=12, max_hp=12))['id']
        trace = []
        lock = game.operation
        orig_acq, orig_rel = lock.acquire, lock.release
        async def acq(*a, **k):
            t = asyncio.current_task().get_name(); trace.append(('want', t)); r = await orig_acq(*a, **k); trace.append(('GOT', t)); return r
        def rel(*a, **k):
            trace.append(('REL', asyncio.current_task().get_name())); return orig_rel(*a, **k)
        lock.acquire, lock.release = acq, rel
        tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=60) as api:
            await asyncio.sleep(0.05)
            body = lambda rid, txt: {'request_id': rid, 'text': txt, 'mode': 'action',
                                     'step': Step(kind='observe', text='Осматриваю', disposition='now').model_dump()}
            slow = asyncio.create_task(api.post('/api/campaigns/%s/actions' % c1, json=body('sl','прислушиваюсь к тишине за дверью')))
            await asyncio.sleep(0.1)              # slow turn is inside operation_without
            print("round", rnd, "locked during model wait:", lock.locked())
            other = asyncio.create_task(api.post('/api/campaigns/%s/actions' % c2, json=body('ot','осматриваюсь')))
            await asyncio.sleep(0.1)              # 'other' is queued on the lock
            print("   'other' still waiting:", not other.done(), "| locked:", lock.locked(), "| owner task:", game._operation_owner.get_name() if game._operation_owner else None)
            await asyncio.gather(slow, other)
        lock.acquire, lock.release = orig_acq, orig_rel
        # did a task that entered via plain 'async with' leave the owner field pointing at itself?
        labels = [t for t in trace if t[0] in ('GOT','REL')]
        print("   lock trace:", labels)
        owner_after = game._operation_owner
        print("   owner field after the round:", owner_after.get_name() if owner_after else None, "| locked:", lock.locked())
        print()

asyncio.run(main())
