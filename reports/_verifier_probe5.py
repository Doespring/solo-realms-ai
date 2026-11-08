
import os, sys, json, time, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests")
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import create_app
from test_dialogue_paging_api import MockLLM
tmp = Path(tempfile.mkdtemp())

def make(n, same_created=None):
    app = create_app(tmp/('h%s.sqlite3'%time.time_ns()), MockLLM())
    st = app.state.store
    with st.connection(True) as c:
        c.execute("INSERT INTO campaigns(id,name,state,master,version,created) VALUES(?,?,?,?,?,?)",
                  ('c1','x',json.dumps({'scene':'двор','turn':n}),'{}',1,time.time()))
        for i in range(n):
            created = same_created if same_created is not None else 4000.0+i
            c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                      ('h%03d'%i,'c1','rq%03d'%i,'t%d'%i,'a%d'%i,'turn','{}',created))
    return app, TestClient(app)

# E. new turn added DURING paging that lands OLDER than the cursor -> must it be seen? It wasn't there when paging began, so it is "backfill". Real risk: a new turn inserted with created equal to an already-shown created but smaller id -> it appears BETWEEN already-fetched pages? Let's simulate the exact real-world case: two rows with identical created (same float, e.g. bulk import), cursor sits between them.
app, api = make(6, same_created=4000.0)
# all created equal: order is id DESC => h005,h004,...,h000
p1 = api.get('/api/campaigns/c1/conversation', params={'limit':2}).json()
print("E p1", [r['id'] for r in p1['conversation']], p1['conversation_cursor'])
# insert a NEW row with the same created and id 'h0055' which sorts between h006..? id DESC: 'h0055' > 'h005'? lexicographic 'h0055' vs 'h005': 'h005' is prefix, shorter sorts first -> 'h0055' > 'h005'
with app.state.store.connection(True) as c:
    c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
              ('h004A','c1','rqX','tx','ax','turn','{}',4000.0))
p2 = api.get('/api/campaigns/c1/conversation', params={'limit':2,'before':json.dumps(p1['conversation_cursor'])}).json()
print("E p2", [r['id'] for r in p2['conversation']], p2['conversation_cursor'])
p3 = api.get('/api/campaigns/c1/conversation', params={'limit':2,'before':json.dumps(p2['conversation_cursor'])}).json()
p4 = api.get('/api/campaigns/c1/conversation', params={'limit':2,'before':json.dumps(p3['conversation_cursor'])}).json()
print("E p3", [r['id'] for r in p3['conversation']], "p4", [r['id'] for r in p4['conversation']])
allseen=[r['id'] for pg in (p1,p2,p3,p4) for r in pg['conversation']]
print("E all:", allseen, "unique", len(set(allseen)), "n", len(allseen))

# F. THE REAL BUG CANDIDATE: 'created' is a float from time.time(); conversation rows
# created AFTER the cursor (newer) are correctly skipped. But what about a row created
# DURING the paging with created SLIGHTLY LESS than last shown (clock skew / retroactive)?
# G. conversation_cursor is rows[0] = OLDEST row on page (oldest-first page) -> fine.

# H. Chromium/browser side: does the page use conversation_cursor from campaign response?
print("--- campaign response cursor check ---")
app2, api2 = make(10)
body = api2.get('/api/campaigns/c1').json()
print("cursor", body['conversation_cursor'], "total", body['conversation_total'], "more", body['conversation_more'], "n conv", len(body['conversation']))
first = body['conversation'][0]['id'] ; last = body['conversation'][-1]['id']
print("campaign conversation first/last:", first, last)
print("campaign cursor equals oldest of campaign page?", body['conversation_cursor'][1]==first)
# Is conversation ordered oldest-first here too? and is 'conversation' limited?
