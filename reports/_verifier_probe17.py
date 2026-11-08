
import os, sys, json, tempfile, asyncio
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero, Step, Plan
from solo.llm import MockLLM
from solo.natural import quick

class Slow(MockLLM):
    delay = 0.5
    async def interpret(self, text, scene):
        await asyncio.sleep(self.delay)
        return await super().interpret(text, scene)
    async def interpret_command(self, text, context):
        await asyncio.sleep(self.delay)
        return await super().interpret_command(text, context)

WORDS = ['осматриваюсь','жду','прислушиваюсь к тишине за дверью']

async def main():
    tmp = Path(tempfile.mkdtemp())
    from solo.contracts import Plan
    plan = Plan(summary='Осматриваюсь', steps=[Step(kind='observe', text='Осматриваю текущую локацию', disposition='now')], needs_confirmation=False)
    app = create_app(tmp/'r.sqlite3', Slow(plan))
    store = app.state.store
    cid = store.create('K', Hero(name='M', hp=12, max_hp=12))['id']
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:19219', timeout=30) as api:
        await asyncio.sleep(0.1)
        # which phrases actually go through the slow model (plan is None -> llm.interpret)?
        st = store.campaign(cid)['state']
        print("natural.quick for candidates:", [(w, quick(w, st) is not None) for w in WORDS])
        v0 = store.campaign(cid)['version']
        print("start version", v0)

        async def one(i, word):
            r = await api.post('/api/campaigns/%s/actions' % cid,
                               json={'request_id':'race%d' % i, 'text': word, 'mode':'action'})
            try:
                body = r.json()
            except Exception:
                body = {'raw': r.text[:200]}
            return i, r.status_code, body

        results = await asyncio.gather(one(1, 'прислушиваюсь к тишине за дверью'), one(2, 'прислушиваюсь к тишине под половицей'))
        for i, code, body in results:
            print("req", i, "->", code, json.dumps(body, ensure_ascii=False)[:300])
        print("final version", store.campaign(cid)['version'], "delta", store.campaign(cid)['version'] - v0)
        evs = store.history(cid)
        print("events:", [(e['id'][:8], e['results'][:0], e.get('narrative_status')) for e in evs])
        acts = store.request(cid, 'race1'), store.request(cid, 'race2')
        print("stored actions:", [(a['id'][:8], a['status'], a['version']) for a in acts if a])
        print("FULL req1:", json.dumps(results[0][2], ensure_ascii=False)[:1500])

asyncio.run(main())

async def race2():
    print()
    print("=== race: first request confirms while the second is still thinking ===")
    tmp = Path(tempfile.mkdtemp())
    plan = Plan(summary='Осматриваюсь', steps=[Step(kind='observe', text='Осматриваю текущую локацию', disposition='now')], needs_confirmation=False)
    class Slow2(Slow):
        delay = 1.0
    app = create_app(tmp/'s.sqlite3', Slow2(plan))
    store = app.state.store
    cid = store.create('K', Hero(name='M', hp=12, max_hp=12))['id']
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:19219', timeout=30) as api:
        await asyncio.sleep(0.1)
        first = asyncio.create_task(api.post('/api/campaigns/%s/actions' % cid,
                  json={'request_id':'f1','text':'осматриваюсь','mode':'action',
                        'step': Step(kind='observe', text='Осматриваю текущую локацию', disposition='now').model_dump()}))
        await asyncio.sleep(0.2)   # f1 finished (no model); now start the slow one
        second = asyncio.create_task(api.post('/api/campaigns/%s/actions' % cid,
                   json={'request_id':'f2','text':'прислушиваюсь к тишине за дверью','mode':'action'}))
        await asyncio.sleep(0.35)  # f2 is inside the model wait now
        r1 = await first
        a1 = r1.json()['action']
        print("f1 action", a1['id'][:8], "status", a1['status'], "version", a1['version'])
        print("version during f2 model wait:", store.campaign(cid)['version'])
        c = await api.post('/api/actions/%s/confirm' % a1['id'])
        print("confirm f1 ->", c.status_code, "version now", store.campaign(cid)['version'])
        r2 = await second
        print("f2 (second player, stale snapshot) ->", r2.status_code, r2.text[:400])

asyncio.run(race2())