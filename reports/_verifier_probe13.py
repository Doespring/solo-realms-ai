
import os, sys, json, tempfile, time
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests")
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Hero
from test_dialogue_paging_api import MockLLM

tmp = Path(tempfile.mkdtemp())
app = create_app(tmp/'n.sqlite3', MockLLM())
store = app.state.store
api = TestClient(app)
cid = store.create('K', Hero(name='M', hp=12, max_hp=12))['id']

def seed(n):
    with store.connection(True) as c:
        for i in range(n):
            c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                      ('h%03d'%i, cid, 'rq%03d'%i, 't%d'%i, 'a%d'%i, 'turn', '{}', 4000.0+i))
seed(60)

# (1) WHY the cursor is the OLDEST row: count what the first page contains
camp = api.get('/api/campaigns/'+cid).json()
conv = camp['conversation']
print("conversation len:", len(conv), "first:", conv[0]['id'], "last:", conv[-1]['id'])
print("cursor:", camp['conversation_cursor'], "more:", camp['conversation_more'])
page = api.get('/api/campaigns/%s/conversation?before=%s&limit=5' % (cid, json.dumps(camp['conversation_cursor']))).json()
print("page1 ids:", [r['id'] for r in page['conversation']], "more:", page['conversation_more'])

# (2) walk the whole history from the campaign response as play.js does
seen = [r['id'] for r in conv]
cur = camp['conversation_cursor']
guard = 0
while camp['conversation_more'] or True:
    p = api.get('/api/campaigns/%s/conversation?before=%s&limit=10' % (cid, json.dumps(cur))).json()
    ids = [r['id'] for r in p['conversation']]
    if not ids: break
    dup = [i for i in ids if i in seen]
    print("  page", ids[0], "..", ids[-1], "dups-with-previous:", dup)
    seen = ids + seen
    cur = p['conversation_cursor']
    guard += 1
    if guard > 10: break
print("walk total:", len(seen), "unique:", len(set(seen)))
