
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

async def main():
    tmp = Path(tempfile.mkdtemp())
    plan = Plan(summary='О', steps=[Step(kind='observe', text='Осматриваю', disposition='now')], needs_confirmation=False)
    app = create_app(tmp/'v.sqlite3', Slow(plan))
    store = app.state.store; game = app.state.game
    c1 = store.create('K1', Hero(name='A', hp=12, max_hp=12))['id']
    c2 = store.create('K2', Hero(name='B', hp=12, max_hp=12))['id']
    log = []
    orig_apply = store.apply
    async def apply_async(*a, **k):
        log.append(('enter', k.get('tag')))
        await asyncio.sleep(0.4)          # a real await inside the critical section
        try: return orig_apply(*a, **k)
        finally: log.append(('leave', k.get('tag')))
    # store.apply is sync; wrap at the game boundary instead, same thing
    orig_route = game.route
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=30) as api:
        await asyncio.sleep(0.1)
        body = lambda rid, txt: {'request_id': rid, 'text': txt, 'mode': 'action',
                                 'step': Step(kind='observe', text='Осматриваю', disposition='now').model_dump()}
        slow = asyncio.create_task(api.post('/api/campaigns/%s/actions' % c1, json=body('s1','прислушиваюсь к тишине за дверью')))
        await asyncio.sleep(0.15)
        print("during slow model wait: operation.locked() =", game.operation.locked())
        other = asyncio.create_task(api.post('/api/campaigns/%s/actions' % c2, json=body('o1','осматриваюсь')))
        await asyncio.sleep(0.1)
        print("while c2 is queued:      operation.locked() =", game.operation.locked(), "| c2 done?", other.done())
        r1 = await slow; r2 = await other
        print("c1", r1.status_code, "c2", r2.status_code)
        print("after both: locked =", game.operation.locked(), "owner None =", game._operation_owner is None)
        # Now: does a fresh, correctly-locking request still get exclusivity?
        marks = []
        async def turn(tag):
            async with game.operation:
                marks.append(('enter', tag))
                await asyncio.sleep(0.3)
                marks.append(('leave', tag))
        await asyncio.gather(turn('A'), turn('B'))
        peak = 0; cur = 0
        for k, t in marks:
            cur += 1 if k == 'enter' else -1; peak = max(peak, cur)
        print("lock exclusivity for two fresh tasks, peak holders:", peak, marks)

asyncio.run(main())
