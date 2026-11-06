
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
    app = create_app(tmp/'r.sqlite3', MockLLM(plan))
    store = app.state.store
    cid = store.create('K', Hero(name='A', hp=12, max_hp=12))['id']
    orig_apply = store.apply
    def slow_apply(*a, **k):
        time.sleep(1.0)          # stands in for a slow commit (rules, sqlite, memory)
        return orig_apply(*a, **k)
    store.apply = slow_apply
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=60) as api:
        await asyncio.sleep(0.05)
        body = {'request_id':'q1','text':'осматриваюсь','mode':'action',
                'step': Step(kind='observe', text='Осматриваю', disposition='now').model_dump()}
        turn = asyncio.create_task(api.post('/api/campaigns/%s/actions' % cid, json=body))
        await asyncio.sleep(0.15)          # turn is now inside the 1 s commit
        t = time.perf_counter()
        h = await api.get('/api/health')   # must NOT need the campaign lock
        took = round(time.perf_counter()-t, 3)
        print("/api/health during a 1.0 s commit took", took, "s ->", h.status_code)
        t = time.perf_counter()
        c = await api.get('/api/campaigns/%s' % cid)
        took2 = round(time.perf_counter()-t, 3)
        print("/api/campaigns/{id} during the same commit took", took2, "s ->", c.status_code)
        await turn
    store.apply = orig_apply
    print()
    print("VERDICT: read requests wait", "BLOCKED on the loop (lock-free in principle, but frozen in practice)" if took > 0.5 or took2 > 0.5 else "still fast")

asyncio.run(main())
