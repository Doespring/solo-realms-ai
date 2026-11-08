
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

def build(n):
    app = create_app(tmp/('g%s.sqlite3'%time.time_ns()), MockLLM())
    cid = app.state.store.create('K', Hero(name='M', hp=12, max_hp=12))['id']
    st = app.state.store
    with st.connection(True) as c:
        for i in range(n):
            c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                      ('h%03d'%i, cid, 'rq%03d'%i, 't%d'%i, 'a%d'%i, 'turn', json.dumps({'journal_id':'ev%03d'%i}), 4000.0+i))
    return app, TestClient(app), cid

for n in (30, 50, 51, 60):
    app, api, cid = build(n)
    camp = api.get('/api/campaigns/'+cid).json()
    conv = camp['conversation']
    print("n=%3d campaign.conversation=%3d more=%s cursor=%s ids=%s..%s" % (n, len(conv), camp['conversation_more'], camp['conversation_cursor'], conv[0]['id'] if conv else None, conv[-1]['id'] if conv else None))
    if camp['conversation_more']:
        p1 = api.get('/api/campaigns/%s/conversation?before=%s' % (cid, json.dumps(camp['conversation_cursor']))).json()
        print("      page1 n=%d ids=%s cursor=%s more=%s" % (len(p1['conversation']), [r['id'] for r in p1['conversation']][:3], p1['conversation_cursor'], p1['conversation_more']))
