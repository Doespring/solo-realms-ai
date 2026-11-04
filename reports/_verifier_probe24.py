
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
    plan = Plan(summary='Реплика', steps=[Step(kind='speak', text='Отвечаю', target='Ирина', disposition='now')], needs_confirmation=False)
    llm = Recorder(plan)
    app = create_app(tmp/'c.sqlite3', llm)
    store = app.state.store
    cid = store.create('Разговор', Hero(name='Мирон', hp=12, max_hp=12))['id']
    tr = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=tr, base_url='http://127.0.0.1:19219', timeout=60) as api:
        await asyncio.sleep(0.05)
        scene = (await api.get('/api/campaigns/%s' % cid)).json()
        npc = scene['scene']['location']['npc'][0]['name'] if scene.get('scene') else 'Ирина'
        print("NPC in scene:", npc)
        ok = 0
        for i in range(45):
            phrase = 'Ирина, я обещаю вернуться к вечеру номер %d и расскажу про дорогу %d' % (i, i)
            r = await api.post('/api/campaigns/%s/actions' % cid, json={'request_id':'d%d'%i,'text':phrase,'mode':'dialogue','target':npc})
            if r.status_code == 200:
                a = r.json()['action']
                if a.get('status') == 'pending':
                    await api.post('/api/actions/%s/confirm' % a['id'])
                ok += 1
            else:
                if i < 3: print("  dialogue", i, r.status_code, r.text[:200])
        print("dialogue turns accepted:", ok, "of 45")
        print("conversation rows:", len(store.conversation_history(cid, limit=500)), "| events:", len(store.history(cid, 200)))
        mem = store.memory.retrieve(cid, audience=[store.memory.identities(cid, store.campaign(cid)['state'])['hero']], query='дорога', limit=10, budget=5000)
        print("memory records matching 'дорога':", len(mem['records']))

        llm.seen.clear()
        r = await api.post('/api/campaigns/%s/actions' % cid, json={'request_id':'measured2','text':'Ирина, а что ты помнишь о моей дороге ???','mode':'dialogue','target':npc})
        if r.status_code == 200 and r.json()['action'].get('status') == 'pending':
            await api.post('/api/actions/%s/confirm' % r.json()['action']['id'])
        print("measured:", r.status_code, r.json().get('action', {}).get('status'))

    print()
    for s in llm.seen:
        if s['channel'] == 'interpret':
            pkt = json.dumps(s['scene'], ensure_ascii=False)
            print("interpret packet chars:", len(pkt))
            print("  packet keys:", list(s['scene'].keys()))
            print("  past_player_words count:", len(s['scene'].get('past_player_words') or []))
            print("  past_player_words:", json.dumps(s['scene'].get('past_player_words'), ensure_ascii=False)[:900])
            print("  memory count:", len(s['scene'].get('memory') or []))
            print("  number of earlier turn numbers mentioned anywhere in the packet:",
                  sorted(set(int(m) for m in re.findall(r'номер (\d+)', pkt))))
            print("  FULL PACKET:", pkt[:3000])
        else:
            u = s['user']
            print("stream user prompt chars:", len(u))
            nums = sorted(set(int(m) for m in re.findall(r'номер (\d+)', u)))
            print("  earlier turn numbers present in the narrator prompt:", nums)
            print("  USER:", u[:2000])

asyncio.run(main())