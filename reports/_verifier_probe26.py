
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
        self.seen.append({'channel':'interpret','text':text,'scene':scene})
        return await super().interpret(text, scene)
    async def stream(self, system, user, **kw):
        self.seen.append({'channel':'stream','system':system,'user':user})
        async for c in super().stream(system, user, **kw): yield c
    def scope(self, campaign, turn_ref):
        from contextlib import nullcontext; return nullcontext()

async def main():
    tmp = Path(tempfile.mkdtemp())
    plan = Plan(summary='Осматриваюсь', steps=[Step(kind='observe', text='Осматриваю текущую локацию', disposition='now')], needs_confirmation=False)
    llm = Recorder(plan)
    app = create_app(tmp/'p.sqlite3', llm)
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
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=120) as api:
        await asyncio.sleep(0.05)
        applied = 0
        for i in range(45):
            phrase = 'ход %d: посмотри на окно и запомни обещание вернуться номер %d' % (i, i)
            r = await api.post('/api/campaigns/%s/play' % cid, json={'request_id':'t%d'%i,'text':phrase})
            if r.status_code == 200 and r.json().get('kind') == 'event': applied += 1
            elif i < 3: print("turn", i, r.status_code, r.text[:200])
        print("event turns applied:", applied, "/45 ; events:", len(store.history(cid, 300)))
        packets.clear(); llm.seen.clear()
        for rid, txt in [('MEASURED','ход 45: снова смотрю на окно'),
                          ('M2','ход 46: что ты помнишь о моём обещании вернуться? что раньше было с окном?'),
                          ('M3','ход 47: прислушиваюсь к разговору и говорю о дороге')]:
            packets.clear(); llm.seen.clear()
            r = await api.post('/api/campaigns/%s/play' % cid, json={'request_id':rid,'text':txt})
            print("=== measured", rid, r.status_code, r.json().get('kind'))
            for s in llm.seen:
                if s['channel'] == 'interpret':
                    t = json.dumps(s['scene'], ensure_ascii=False); d = s['scene']
                    print("   interpret packet chars:", len(t), "keys:", list(d.keys()))
                    print("   past_player_words:", len(d.get('past_player_words') or []), json.dumps(d.get('past_player_words'), ensure_ascii=False)[:700])
                    print("   memory records:", len(d.get('memory') or []))
                    print("   turn numbers in packet:", sorted(set(int(m) for m in re.findall(r'номер (\d+)', t))))
                else:
                    u = s['user']
                    print("   narrator user chars:", len(u), "| phrases of the 45 present:", sum(1 for i in range(45) if ('обещание вернуться номер %d' % i) in u))
            for system, par, master in packets:
                d = json.loads(par); t = json.dumps(d, ensure_ascii=False)
                print("   narrator packet chars:", len(par), "keys:", list(d.keys()))
                print("   past_player_words:", len(d.get('past_player_words') or []), json.dumps(d.get('past_player_words'), ensure_ascii=False)[:900])
                print("   memory records:", len(d.get('memory') or []))
                print("   turn numbers in packet:", sorted(set(int(m) for m in re.findall(r'номер (\d+)', t))))
    SC.narrator = orig

asyncio.run(main())