
import os, sys, asyncio, json, tempfile, re
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero, Step, Plan
from solo.llm import MockLLM

class Rec(MockLLM):
    def __init__(self,*a,**k):
        super().__init__(*a,**k); self.seen=[]
    async def interpret(self,t,s):
        self.seen.append(('interpret',t,json.loads(json.dumps(s,ensure_ascii=False,default=str))))
        return await super().interpret(t,s)
    async def stream(self,sys_,u,**k):
        self.seen.append(('stream',sys_,u))
        async for c in super().stream(sys_,u,**k): yield c
    def scope(self,c,t):
        from contextlib import nullcontext; return nullcontext()

async def main():
    tmp = Path(tempfile.mkdtemp())
    plan = Plan(summary='Осматриваюсь', steps=[Step(kind='observe', text='Осматриваю', disposition='now')], needs_confirmation=False)
    llm = Rec(plan)
    app = create_app(tmp/'q.sqlite3', llm)
    store = app.state.store; game = app.state.game
    cid = store.create('Д', Hero(name='Мирон', hp=12, max_hp=12))['id']
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=120) as api:
        await asyncio.sleep(0.05)
        for i in range(45):
            await api.post('/api/campaigns/%s/play' % cid, json={'request_id':'k%d'%i,'text':'ход %d: обещаю Ирине вернуться номер %d' % (i,i)})
        camp = store.campaign(cid)
        ident = store.memory.identities(cid, camp['state'])
        print("identities:", {k:(v[:8] if isinstance(v,str) else v) for k,v in ident.items()})
        for q in ['обещание', 'вернуться', 'номер 7', 'дорога']:
            got = store.memory.retrieve(cid, audience=[ident['hero']], query=q, limit=10, budget=5000)
            print("memory.retrieve(%r): %d records" % (q, len(got['records'])),
                  [ (r['kind'], r['content'][:40]) for r in got['records'][:3] ])
            got2 = store.memory.retrieve(cid, audience=[ident['hero']], query=q, location=ident['location'], limit=10, budget=5000)
            print("   with location filter: %d records" % len(got2['records']))
        # memory is only consulted when the model is asked (plan is None). Force that:
        llm.seen.clear()
        r = await api.post('/api/campaigns/%s/play' % cid, json={'request_id':'zz','text':'что ты помнишь о моём обещании вернуться, Ирина?'})
        print("forced-model turn:", r.status_code, r.json().get('kind'))
        for kind, a, b in llm.seen:
            print("  ", kind, (json.dumps(b, ensure_ascii=False)[:600] if kind=='interpret' else len(b)))

asyncio.run(main())
