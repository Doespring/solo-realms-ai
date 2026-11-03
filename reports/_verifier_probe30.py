
import os, sys, asyncio, json, tempfile, re
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero
from solo.preview_mock import PreviewMock

async def main():
    tmp = Path(tempfile.mkdtemp())
    llm = PreviewMock()
    app = create_app(tmp/'g.sqlite3', llm)
    store = app.state.store; game = app.state.game
    cid = store.create('Длинная', Hero(name='Мирон', hp=12, max_hp=12))['id']
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=180) as api:
        await asyncio.sleep(0.05)
        for i in range(45):
            await api.post('/api/campaigns/%s/play' % cid, json={'request_id':'g%d'%i,'text':'осматриваюсь %d' % i})
        camp = store.campaign(cid)
        system, body = game.compiler.gm(camp, 'что известно о брате?')
        d = json.loads(body)
        print("GM packet chars:", len(body), "| system chars:", len(system))
        print("  keys:", list(d.keys()))
        print("  last_results entries:", len(d.get('last_results') or []))
        print("  history default limit is 50 -> bounded by", 50)
        print("  turn numbers visible:", sorted(set(int(m) for m in re.findall(r'осматриваюсь (\d+)', body))))
        # what the narrator seeds at turn 0 (the §9.1 gap): packet['narrative'] mentions turn 0 explicit setting
        print()
        h = store.history(cid, 50)
        print("history rows:", len(h), "| any row with narrative_status complete:", sum(1 for r in h if r.get('narrative_status')=='complete'))

asyncio.run(main())