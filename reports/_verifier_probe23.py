
import os, sys, asyncio, json, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero, Step, Plan
from solo.llm import MockLLM

class Recorder(MockLLM):
    """Records exactly what the game hands the model, then behaves like MockLLM."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.seen = []
    async def interpret(self, text, scene):
        self.seen.append({'channel': 'interpret', 'text': text, 'scene': scene})
        return await super().interpret(text, scene)
    async def stream(self, system, user, **kw):
        self.seen.append({'channel': 'stream', 'system': system, 'user': user})
        async for chunk in super().stream(system, user, **kw):
            yield chunk
    def scope(self, campaign, turn_ref):
        from contextlib import nullcontext
        return nullcontext()

async def main():
    tmp = Path(tempfile.mkdtemp())
    plan = Plan(summary='Осматриваюсь', steps=[Step(kind='observe', text='Осматриваю текущую локацию', disposition='now')], needs_confirmation=False)
    llm = Recorder(plan)
    app = create_app(tmp/'h.sqlite3', llm)
    store = app.state.store
    cid = store.create('Длинная', Hero(name='Мирон', hp=12, max_hp=12))['id']

    # 45 real turns through the ordinary dialogue endpoint
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=60) as api:
        await asyncio.sleep(0.05)
        made = 0
        for i in range(45):
            phrase = 'ход номер %d: прислушиваюсь к тишине за дверью %d' % (i, i)
            r = await api.post('/api/campaigns/%s/actions' % cid, json={'request_id': 'turn%d' % i, 'text': phrase, 'mode': 'action'})
            if r.status_code == 200:
                a = r.json()['action']
                if a.get('status') == 'pending':
                    c = await api.post('/api/actions/%s/confirm' % a['id'])
                    if c.status_code != 200: print("   confirm failed", c.status_code, c.text[:120])
            if r.status_code == 200:
                made += 1
            else:
                print("turn", i, r.status_code, r.text[:150])
        stored = store.conversation_history(cid, limit=200)
        events = store.history(cid, 200)
        print("turns attempted:", 45, "accepted:", made)
        print("rows in conversations:", len(stored), "| events:", len(events))

        # the measured turn
        llm.seen.clear()
        r = await api.post('/api/campaigns/%s/actions' % cid, json={'request_id': 'measured', 'text': 'ход для замера: смотрю на дверь и прислушиваюсь', 'mode': 'action'})
        if r.status_code == 200 and r.json()['action'].get('status') == 'pending':
            r = await api.post('/api/actions/%s/confirm' % r.json()['action']['id'])
            print("  [measured action confirmed]")
        print("measured turn:", r.status_code, r.text[:120])

    print()
    print("=== what the model received this turn ===")
    for s in llm.seen:
        blob = json.dumps(s, ensure_ascii=False)
        print("channel:", s['channel'], "chars:", len(blob))
        if s['channel'] == 'interpret':
            print("  scene keys:", list(s['scene'].keys()))
            print("  past_player_words:", json.dumps(s['scene'].get('past_player_words'), ensure_ascii=False)[:800])
            print("  memory records:", len(s['scene'].get('memory') or []))
            print("  FULL SCENE:", json.dumps(s['scene'], ensure_ascii=False)[:2500])
        else:
            usr = s['user']
            print("  USER PROMPT chars:", len(usr))
            print("  how many of the 46 phrases appear in the user prompt:",
                  sum(1 for i in range(46) if ('прислушиваюсь' in usr and str(i) in usr)))
            print("  USER:", usr[:3000])

asyncio.run(main())