
import os, sys, json, time, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests")
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import create_app
from test_dialogue_paging_api import MockLLM
from solo.contracts import Hero
tmp = Path(tempfile.mkdtemp())

# Mimic a real dialogue turn: journal event 'ev<N>' + conversation row with context.journal_id = ev<N>
def build(n):
    app = create_app(tmp/('g%s.sqlite3'%time.time_ns()), MockLLM())
    cid = app.state.store.create('K', Hero(name='M', hp=12, max_hp=12))['id']
    st = app.state.store
    with st.connection(True) as c:
        for i in range(n):
            c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                      ('h%03d'%i, cid, 'rq%03d'%i, 't%d'%i, 'a%d'%i, 'turn', json.dumps({'journal_id':'ev%03d'%i}), 4000.0+i))
    return app, TestClient(app), cid

app, api, cid = build(30)
camp = api.get('/api/campaigns/'+cid).json()
print("campaign: n conv", len(camp['conversation']), "more", camp['conversation_more'], "has journal_event?", 'journal_event' in camp['conversation'][0])
print("cursor:", camp['conversation_cursor'], "keys of row:", sorted(camp['conversation'][0].keys()))
p1 = api.get('/api/campaigns/%s/conversation?before=%s' % (cid, json.dumps(camp['conversation_cursor']))).json()
print("page1:", [r['id'] for r in p1['conversation']][:3], 'keys:', sorted(p1['conversation'][0].keys()))
print("page1 has journal_event?", 'journal_event' in p1['conversation'][0])
p2 = api.get('/api/campaigns/%s/conversation?before=%s' % (cid, json.dumps(p1['conversation_cursor']))).json()
ids = [r['id'] for r in p1['conversation']] + [r['id'] for r in p2['conversation']]
print("page2 more=True?", p2['conversation_more'])
p3 = api.get('/api/campaigns/%s/conversation?before=%s' % (cid, json.dumps(p2['conversation_cursor']))).json()
ids += [r['id'] for r in p3['conversation']]
print("p3 more:", p3['conversation_more'], "ids:", ids)
