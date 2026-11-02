
import os, sys, asyncio, json, tempfile, re
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero
from solo.preview_mock import PreviewMock

def report(ctx, body_or_text, system=''):
    d = json.loads(body_or_text) if body_or_text.strip().startswith('{') else {}
    t = json.dumps(d, ensure_ascii=False) if d else body_or_text
    print("  chars:", len(t) + len(system), "| keys:", list(d.keys()) if d else '(raw text)')
    if d:
        print("  memory records:", len(d.get('memory') or []))
        print("  past_player_words:", len(d.get('past_player_words') or []), json.dumps(d.get('past_player_words') or [], ensure_ascii=False)[:500])
        print("  world.observed_events:", len((d.get('world') or {}).get('observed_events') or []))
        print("  confirmed:", len(d.get('confirmed') or []))
    print("  previous turns visible anywhere:", sorted(set(int(m) for m in re.findall(r'осматриваюсь (\d+)', t))))

async def main():
    tmp = Path(tempfile.mkdtemp())
    llm = PreviewMock()
    app = create_app(tmp/'q.sqlite3', llm)
    store = app.state.store; game = app.state.game
    cid = store.create('Длинная', Hero(name='Мирон', hp=12, max_hp=12))['id']
    SC = type(game.compiler)
    packets = []
    orig = SC.narrator
    def spy(self, campaign, event):
        out = orig(self, campaign, event); packets.append(out); return out
    SC.narrator = spy
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    event_id = None
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=180) as api:
        await asyncio.sleep(0.05)
        for i in range(45):
            r = await api.post('/api/campaigns/%s/play' % cid, json={'request_id':'s%d'%i,'text':'осматриваюсь %d' % i})
            if r.status_code == 200 and r.json().get('kind') == 'event': event_id = r.json()['event']['id']
        print("events:", len(store.history(cid, 300)), "| last event:", event_id)
        packets.clear(); llm.packets.clear()
        r = await api.post('/api/events/%s/narrate' % event_id, json={'regenerate': False})
        print("narrate:", r.status_code, r.text[:80])
    SC.narrator = orig
    print()
    print("=== NARRATOR prompt on the last turn ===", "packets:", len(packets))
    for system, body, master in packets[:1]:
        report(None, body, system)
        print("  FULL:", body[:2600])
    print()
    print("=== INTERPRETER prompt seen by the model on the same turn ===", "llm.packets:", len(llm.packets))
    if llm.packets:
        p = llm.packets[-1]
        print("  keys:", list(p.keys()))
        ctx = p.get('packet', p)
        report(None, json.dumps(ctx, ensure_ascii=False, default=str))
        print("  FULL:", json.dumps(ctx, ensure_ascii=False, default=str)[:2200])

asyncio.run(main())
