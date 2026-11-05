import os, sys, asyncio, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r'C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI')
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero
from solo.preview_mock import PreviewMock

async def main():
    tmp = Path(tempfile.mkdtemp())
    app = create_app(tmp/'e.sqlite3', PreviewMock())
    store = app.state.store
    cid = store.create('E2E', Hero(name='A', hp=12, max_hp=12))['id']
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=120) as api:
        await asyncio.sleep(0.05)
        for i in range(3):
            rr = await api.post('/api/campaigns/%s/play' % cid, json={'request_id':'e%d'%i,'text':'осматриваюсь %d' % i})
            print('turn', i, rr.status_code, rr.text[:220])
        stack = app.routes
        for p in ['/api/campaigns/%s/conversation' % cid, '/api/campaigns/%s' % cid]:
            r2 = await api.get(p)
            print(p, r2.status_code, r2.text[:300])

asyncio.run(main())
