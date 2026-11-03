
import os, sys, json, tempfile, asyncio
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero, Step
from solo.app import ActionRequest
from solo.llm import MockLLM

class Slow(MockLLM):
    delay = 0.4
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls = []
    async def interpret(self, text, scene):
        self.calls.append(('interpret', text))
        await asyncio.sleep(self.delay)
        return await super().interpret(text, scene)
    async def interpret_command(self, text, context):
        self.calls.append(('interpret_command', text))
        await asyncio.sleep(self.delay)
        return await super().interpret_command(text, context)

async def main():
    tmp = Path(tempfile.mkdtemp())
    llm = Slow()
    app = create_app(tmp/'r.sqlite3', llm)
    store = app.state.store
    cid = store.create('K', Hero(name='M', hp=12, max_hp=12))['id']
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:19219') as api:
        await asyncio.sleep(0.1)
        v0 = store.campaign(cid)['version']
        step = Step(kind='observe', text='Осматриваю текущую локацию')
        r = await api.post('/api/campaigns/%s/actions' % cid,
                           json={'request_id':'a1','text':'осматриваюсь','mode':'action','step': step.model_dump()})
        print("single step action:", r.status_code, r.text[:400])
        a = ActionRequest.model_validate({'request_id':'x1','text':'осматриваюсь','mode':'action'})
        print("ActionRequest text:", a.text)

asyncio.run(main())