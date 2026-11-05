
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
app = create_app(tmp/'i.sqlite3', MockLLM())
cid = app.state.store.create('K', Hero(name='M', hp=12, max_hp=12))['id']
st = app.state.store
with st.connection(True) as c:
    for i in range(5):
        c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                  ('h%03d'%i, cid, 'rq%03d'%i, 't%d'%i, 'a%d'%i, 'turn', json.dumps({'journal_id':'ev%03d'%i}), 4000.0+i))
api = TestClient(app)
camp = api.get('/api/campaigns/'+cid).json()
print("campaign conv rows:", [(r['id'], r.get('journal_event'), 'context' in r) for r in camp['conversation']])
pg = api.get('/api/campaigns/%s/conversation?limit=2' % cid).json()
print("page rows:", [(r['id'], r.get('journal_event'), 'context' in r) for r in pg['conversation']])
print()
print("timeline (as play.js would build it): journal events:", len(camp['history']), "conversation rows:", len(camp['conversation']))
print("Rows that WOULD be skipped by the journal_event filter:", sum(1 for r in camp['conversation'] if r.get('journal_event')))
print("Rows that WOULD be printed as the Master's line twice:", sum(1 for r in pg['conversation'] if not r.get('journal_event')))
