
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

app = create_app(tmp/'e.sqlite3', MockLLM())
cid = app.state.store.create('K', Hero(name='Miron', hp=12, max_hp=12))['id']
st = app.state.store
with st.connection(True) as c:
    for i in range(30):
        c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                  ('h%03d'%i, cid, 'rq%03d'%i, 't%d'%i, 'a%d'%i, 'turn','{}', 4000.0+i))
api = TestClient(app)

camp = api.get('/api/campaigns/'+cid).json()
print("campaign page: n conv =", len(camp['conversation']), "more=", camp['conversation_more'], "cursor=", camp['conversation_cursor'])

# Page1 from campaign cursor
p1 = api.get('/api/campaigns/%s/conversation?before=%s' % (cid, json.dumps(camp['conversation_cursor']))).json()
print("page1 oldest-first ids:", [r['id'] for r in p1['conversation']][:3], "...", [r['id'] for r in p1['conversation']][-3:])
print("page1 cursor:", p1['conversation_cursor'])

# Now: NEW turn arrives between the two requests (simulating player sending during load)
with st.connection(True) as c:
    c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
              ('hNEW', cid, 'rqNEW','tn','an','turn','{}', time.time()))

p2 = api.get('/api/campaigns/%s/conversation?before=%s' % (cid, json.dumps(p1['conversation_cursor']))).json()
print("page2 ids:", [r['id'] for r in p2['conversation']])
allids = [r['id'] for r in p1['conversation']] + [r['id'] for r in p2['conversation']]
print("dupes:", len(allids)!=len(set(allids)))

# STEP 1 probe: does an EXISTING campaign carry a conversation_cursor while old turns remain, and after paging to the very beginning does 'more' become False?
print()
print("=== Scenario: page to the end, then send a new turn, then press load-older again ===")
app2 = create_app(tmp/'f.sqlite3', MockLLM())
cid2 = app2.state.store.create('K', Hero(name='M', hp=12, max_hp=12))['id']
st2 = app2.state.store
with st2.connection(True) as c:
    for i in range(8):
        c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                  ('h%03d'%i, cid2, 'rq%03d'%i, 't%d'%i, 'a%d'%i, 'turn','{}', 4000.0+i))
api2 = TestClient(app2)
camp2 = api2.get('/api/campaigns/'+cid2).json()
cur = camp2['conversation_cursor']; more = camp2['conversation_more']
seen = [r['id'] for r in camp2['conversation']]
pages = 0
while more and pages < 10:
    pages += 1
    pg = api2.get('/api/campaigns/%s/conversation?before=%s' % (cid2, json.dumps(cur))).json()
    seen = [r['id'] for r in pg['conversation']] + seen
    cur = pg['conversation_cursor']; more = pg['conversation_more']
print("walked pages:", pages, "ids:", seen, "unique", len(set(seen)))
