
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
    app = create_app(tmp/'n.sqlite3', llm)
    store = app.state.store; game = app.state.game
    cid = store.create('Длинная', Hero(name='Мирон', hp=12, max_hp=12))['id']
    SC = type(game.compiler)
    packets = []
    orig = SC.narrator
    def spy(self, campaign, event):
        out = orig(self, campaign, event)
        packets.append(out)
        return out
    SC.narrator = spy
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=180) as api:
        await asyncio.sleep(0.05)
        ok = 0
        for i in range(45):
            r = await api.post('/api/campaigns/%s/play' % cid, json={'request_id':'n%d'%i,'text':'осматриваюсь %d' % i})
            if r.status_code == 200 and r.json().get('kind') == 'event': ok += 1
            elif i < 3: print("  ", i, r.status_code, r.text[:160])
        print("event turns:", ok, "/45 ; events:", len(store.history(cid, 300)))
        packets.clear()
        r = await api.post('/api/campaigns/%s/play' % cid, json={'request_id':'FINAL','text':'осматриваюсь ещё раз'})
        print("final turn:", r.status_code, r.json().get('kind'))
    SC.narrator = orig
    print("narrator packets:", len(packets))
    for system, body, master in packets[:1]:
        d = json.loads(body)
        t = json.dumps(d, ensure_ascii=False)
        print("  narrator packet chars:", len(body), "| system chars:", len(system))
        print("  keys:", list(d.keys()))
        print("  memory records:", len(d.get('memory') or []))
        print("  past_player_words:", len(d.get('past_player_words') or []))
        print("  confirmed facts:", len(d.get('confirmed') or []))
        print("  observed_events:", len(d.get('world', {}).get('observed_events') or []))
        print("  previous-turn numbers present:", sorted(set(int(m) for m in re.findall(r'осматриваюсь (\d+)', t))))
        print("  FULL PACKET:", t[:2200])

asyncio.run(main())
