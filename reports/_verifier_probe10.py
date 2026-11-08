
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
app = create_app(tmp/'k.sqlite3', MockLLM())
cid = app.state.store.create('K', Hero(name='M', hp=12, max_hp=12))['id']
st = app.state.store
with st.connection(True) as c:
    for i in range(5):
        c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                  ('h%03d'%i, cid, 'rq%03d'%i, 't%d'%i, 'a%d'%i, 'turn', json.dumps({'journal_id':'ev%03d'%i}), 4000.0+i))
        c.execute("INSERT INTO actions(id,campaign,request_id,original,plan,status,version,metrics,created) VALUES(?,?,?,?,?,?,?,?,?)",
                  ('a%03d'%i, cid, 'rq%03d'%i, 't%d'%i, '{}', 'applied', i+1, '{}', 4000.0+i))
        c.execute("INSERT INTO events(id,action,campaign,before_state,after_state,results,narrative,narrative_status,error,undone,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  ('ev%03d'%i, 'a%03d'%i, cid, '{}', '{}', '[]', 'Story %d'%i, 'complete', '', 0, 4000.0+i))
api = TestClient(app)
camp = api.get('/api/campaigns/'+cid).json()
print("campaign rows (id, journal_event):", [(r['id'], r.get('journal_event')) for r in camp['conversation']])
pg = api.get('/api/campaigns/%s/conversation?limit=2' % cid).json()
print("page limit=2 rows:", [(r['id'], r.get('journal_event'), 'context' in r) for r in pg['conversation']])
pg5 = api.get('/api/campaigns/%s/conversation?limit=5' % cid).json()
print("page limit=5 rows:", [(r['id'], r.get('journal_event'), 'context' in r) for r in pg5['conversation']])
# Do a full campaign response: what does history carry?
print("history ids:", [e['id'] for e in camp['history']])
