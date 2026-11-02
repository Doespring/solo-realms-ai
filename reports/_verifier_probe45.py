import os, sys, asyncio, json, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r'C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI')
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero, Step, Plan
from solo.preview_mock import PreviewMock
from solo.llm import MockLLM

async def main():
    tmp = Path(tempfile.mkdtemp())
    plan = Plan(summary='О', steps=[Step(kind='observe', text='Осматриваю', disposition='now')], needs_confirmation=False)
    app = create_app(tmp/'e.sqlite3', PreviewMock())
    store = app.state.store
    cid = store.create('E2E', Hero(name='A', hp=12, max_hp=12))['id']
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=120) as api:
        await asyncio.sleep(0.05)
        for i in range(3):
            rr = await api.post('/api/campaigns/%s/play' % cid, json={'request_id':'e%d'%i,'text':'осматриваюсь %d' % i})
            if rr.status_code != 200 or rr.json().get('kind') != 'event': print('  turn', i, rr.status_code, rr.text[:160])
        pr = await api.get('/api/campaigns/%s/conversation' % cid, params={'limit': 50})
        page = pr.json()['conversation']
        cr = await api.get('/api/campaigns/%s' % cid)
        camp = cr.json()
        print('real turns:', len(page))
        print('paged rows with journal_event:', sum(1 for r in page if r.get('journal_event')), '/', len(page))
        cc = camp.get('conversation', [])
        print('campaign rows with journal_event:', sum(1 for r in cc if r.get('journal_event')), '/', len(cc))
        print('sample paged row keys:', sorted(page[0].keys()))
        print('sample campaign row keys:', sorted(cc[0].keys()) if cc else 'none')

asyncio.run(main())