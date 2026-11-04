
import os, sys, asyncio, json, tempfile, re
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
import httpx
from solo.app import create_app
from solo.contracts import Hero, Step, Plan
from solo.llm import MockLLM

class Recorder(MockLLM):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.seen = []
    async def interpret(self, text, scene):
        self.seen.append({'channel':'interpret','text':text,'scene':json.loads(json.dumps(scene, ensure_ascii=False, default=str))})
        return await super().interpret(text, scene)
    async def stream(self, system, user, **kw):
        self.seen.append({'channel':'stream','system':system,'user':user})
        async for c in super().stream(system, user, **kw): yield c
    def scope(self, campaign, turn_ref):
        from contextlib import nullcontext; return nullcontext()

async def main():
    tmp = Path(tempfile.mkdtemp())
    plan = Plan(summary='Реплика', steps=[Step(kind='speak', text='Отвечаю', target='Ирина', disposition='now')], needs_confirmation=False)
    llm = Recorder(plan)
    app = create_app(tmp/'c2.sqlite3', llm)
    store = app.state.store; game = app.state.game
    cid = store.create('Разговор', Hero(name='Мирон', hp=12, max_hp=12))['id']
    # instrument the real packet builder: what does scene() hand the model?
    packets = []
    SC = type(game.compiler)
    orig_scene = SC.narrator
    def spy_scene(self, campaign, event):
        out = orig_scene(self, campaign, event)
        packets.append(out)
        return out
    SC.narrator = spy_scene
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=60) as api:
        await asyncio.sleep(0.05)
        for i in range(45):
            phrase = 'Ирина, я обещаю вернуться к вечеру номер %d и расскажу про дорогу %d' % (i, i)
            r = await api.post('/api/campaigns/%s/actions' % cid, json={'request_id':'e%d'%i,'text':phrase,'mode':'dialogue','target':'Ирина'})
            if r.status_code == 200 and r.json()['action'].get('status') == 'pending':
                await api.post('/api/actions/%s/confirm' % r.json()['action']['id'])
        packets.clear(); llm.seen.clear()
        r = await api.post('/api/campaigns/%s/actions' % cid, json={'request_id':'meas','text':'Ирина, а что ты помнишь о моей дороге ?','mode':'dialogue','target':'Ирина'})
        print("measured HTTP:", r.status_code, r.text[:600])
        if r.status_code == 200 and r.json()['action'].get('status') == 'pending':
            c = await api.post('/api/actions/%s/confirm' % r.json()['action']['id'])
            print("confirm:", c.status_code, c.text[:600])
    SC.narrator = orig_scene
    print("narrator() calls on the measured turn:", len(packets), "| llm calls:", [s['channel'] for s in llm.seen])
    for system, body, master in packets:
        print("packet chars:", len(body), "| system chars:", len(system), "| master chars:", len(master) if master else 0)
        d = json.loads(body)
        print("  keys:", list(d.keys()))
        ppw = d.get('past_player_words') or []
        print("  past_player_words:", len(ppw), "->", json.dumps(ppw, ensure_ascii=False)[:900])
        print("  memory records in packet:", len(d.get('memory') or []))
        body_txt = json.dumps(d, ensure_ascii=False)
        print("  turn numbers present anywhere in packet:", sorted(set(int(m) for m in re.findall(r'номер (\d+)', body_txt))))
        print("  total chars sent to the model (system+packet):", len(system)+len(body))

asyncio.run(main())