
import os, sys, asyncio, tempfile, time
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero, Step, Plan
from solo.llm import MockLLM

async def main():
    tmp = Path(tempfile.mkdtemp())
    plan = Plan(summary='О', steps=[Step(kind='observe', text='Осматриваю', disposition='now')], needs_confirmation=False)
    app = create_app(tmp/'w.sqlite3', MockLLM(plan))
    store = app.state.store; game = app.state.game
    c1 = store.create('K1', Hero(name='A', hp=12, max_hp=12))['id']
    c2 = store.create('K2', Hero(name='B', hp=12, max_hp=12))['id']
    events = []
    t0 = time.perf_counter()
    orig_apply = store.apply
    def slow_apply(*a, **k):
        events.append(('ENTER', round(time.perf_counter()-t0,3), id(asyncio.current_task())))
        time.sleep(0.3)
        try: return orig_apply(*a, **k)
        finally: events.append(('LEAVE', round(time.perf_counter()-t0,3), id(asyncio.current_task())))
    store.apply = slow_apply
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=60) as api:
        await asyncio.sleep(0.05)
        body = lambda rid: {'request_id': rid, 'text': 'осматриваюсь', 'mode': 'action',
                            'step': Step(kind='observe', text='Осматриваю', disposition='now').model_dump()}
        await asyncio.gather(api.post('/api/campaigns/%s/actions' % c1, json=body('p1')),
                             api.post('/api/campaigns/%s/actions' % c2, json=body('p2')))
    store.apply = orig_apply
    print("mutation windows:", events)
    # count overlaps
    depth = peak = 0
    for kind, at, tid in events:
        depth += 1 if kind == 'ENTER' else -1
        peak = max(peak, depth)
    print("PEAK CONCURRENT MUTATION SECTIONS:", peak)
    print("VERDICT:", "lock does NOT serialise mutations" if peak > 1 else "mutations were serialised")

asyncio.run(main())
