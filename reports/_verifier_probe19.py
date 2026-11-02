
import os, sys, asyncio, json, tempfile, traceback
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
    for round_ in range(6):
        tmp = Path(tempfile.mkdtemp())
        plan = Plan(summary='О', steps=[Step(kind='observe', text='Осматриваю текущую локацию', disposition='now')], needs_confirmation=False)
        app = create_app(tmp/'u.sqlite3', Slow(plan))
        store = app.state.store
        c1 = store.create('K1', Hero(name='A', hp=12, max_hp=12))['id']
        c2 = store.create('K2', Hero(name='B', hp=12, max_hp=12))['id']
        game = app.state.game
        tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=30) as api:
            await asyncio.sleep(0.1)
            # campaign 1 goes into a slow model wait (lock released inside operation_without)
            slow = asyncio.create_task(api.post('/api/campaigns/%s/actions' % c1,
                       json={'request_id':'s1','text':'прислушиваюсь к тишине за дверью','mode':'action'}))
            await asyncio.sleep(0.15)
            # campaign 2 tries a mutating turn in the meantime -> queues on game.operation
            other = asyncio.create_task(api.post('/api/campaigns/%s/actions' % c2,
                       json={'request_id':'o1','text':'осматриваюсь','mode':'action',
                             'step': Step(kind='observe', text='Осматриваю текущую локацию', disposition='now').model_dump()}))
            r1 = await slow
            r2 = await other
            print("round", round_, "| c1:", r1.status_code, r1.text[:80].replace('\n',' '),
                  "| c2:", r2.status_code, r2.text[:160].replace('\n',' '))
            print("   operation.locked() =", game.operation.locked(), "owner is None?", game._operation_owner is None)
            # The decisive probe: does the lock now admit a SECOND holder while one
            # task is still inside its critical section?
            fence = []
            orig_apply = store.apply
            def slow_apply(*a, **k):
                fence.append(('enter', id(asyncio.current_task())))
                import time as _t; _t.sleep(0.35)
                try: return orig_apply(*a, **k)
                finally: fence.append(('leave', id(asyncio.current_task())))
            store.apply = slow_apply
            tA = asyncio.create_task(api.post('/api/campaigns/%s/actions' % c2,
                     json={'request_id':'z1','text':'осматриваюсь','mode':'action',
                           'step': Step(kind='observe', text='Осматриваю', disposition='now').model_dump()}))
            await asyncio.sleep(0.05)
            tB = asyncio.create_task(api.post('/api/campaigns/%s/actions' % c1,
                     json={'request_id':'z2','text':'осматриваюсь','mode':'action',
                           'step': Step(kind='observe', text='Осматриваю', disposition='now').model_dump()}))
            await asyncio.gather(tA, tB)
            store.apply = orig_apply
            inside = 0; peak = 0
            for kind, tid in fence:
                inside += 1 if kind == 'enter' else -1
                peak = max(peak, inside)
            print("   MUTATION SECTION overlap, peak concurrent holders:", peak, fence)
            # a third request afterwards: is the lock permanently broken?
            r3 = await api.post('/api/campaigns/%s/actions' % c2,
                       json={'request_id':'o2','text':'осматриваюсь','mode':'action',
                             'step': Step(kind='observe', text='Осматриваю', disposition='now').model_dump()})
            print("   third request:", r3.status_code, r3.text[:120].replace('\n',' '))

asyncio.run(main())