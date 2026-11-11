
import os, sys, asyncio, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero, Step, Plan
from solo.llm import MockLLM

class Tracer(MockLLM):
    def __init__(self,*a,**k):
        super().__init__(*a,**k); self.fired=[]
    async def interpret(self, text, scene):
        await asyncio.sleep(0.2)
        return await super().interpret(text, scene)

async def run(label, mutate):
    tmp = Path(tempfile.mkdtemp())
    plan = Plan(summary='О', steps=[Step(kind='observe', text='Осматриваю', disposition='now')], needs_confirmation=False)
    llm = Tracer(plan)
    app = create_app(tmp/'m.sqlite3', llm)
    store, game = app.state.store, app.state.game
    cid = store.create('K', Hero(name='A', hp=12, max_hp=12))['id']
    mutate(game)
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=60) as api:
        await asyncio.sleep(0.05)
        # a plain 'async with game.operation' path that never registered as owner
        # (this is what game.py:716/935/1003/1056 look like)
        async def incognito():
            async with game.operation:
                pass
        await incognito()
        r = await api.post('/api/campaigns/%s/play' % cid,
                           json={'request_id':'x','text':'осматриваюсь'})
    print(label, "->", r.status_code, r.json().get('kind'), "|", str(r.json().get('reply') or r.json().get('message') or '')[:110])

async def main():
    await run("clean owner field   ", lambda g: None)
    await run("stale owner (other) ", lambda g: setattr(g, '_operation_owner', object()))

asyncio.run(main())
