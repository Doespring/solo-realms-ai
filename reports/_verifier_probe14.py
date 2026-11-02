
import os, sys, json, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests")
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Hero
from test_dialogue_paging_api import MockLLM

def fresh(name):
    tmp = Path(tempfile.mkdtemp())
    app = create_app(tmp/(name+'.sqlite3'), MockLLM())
    return app, app.state.store, TestClient(app)

def ins(store, cid, rows):
    with store.connection(True) as c:
        for rid, created in rows:
            c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                      (rid, cid, 'r'+rid, rid, 'a'+rid, 'turn', '{}', created))

# A) tied bucket LARGER than the window (all 60 at 4000.0)
app, store, api = fresh('a')
cid = store.create('K', Hero(name='M', hp=12, max_hp=12))['id']
ins(store, cid, [('h%03d'%i, 4000.0) for i in range(60)])
camp = api.get('/api/campaigns/'+cid).json()
win = [r['id'] for r in camp['conversation']]
print("A) all-60-tied: window =", win)
print("   cursor", camp['conversation_cursor'], "more", camp['conversation_more'])
p = api.get('/api/campaigns/%s/conversation?before=%s' % (cid, json.dumps(camp['conversation_cursor']))).json()
print("   older page =", [r['id'] for r in p['conversation']], "more", p['conversation_more'])
print("   DUPES =", sorted(set(win) & set(r['id'] for r in p['conversation'])))
print("   LOST  =", sorted(set('h%03d'%i for i in range(60)) - set(win) - set(r['id'] for r in p['conversation'])))

# B) tie SPANS the cut: 48 old + 10 tied at 7000 (cut lands inside the tie)
app, store, api = fresh('b')
cid = store.create('K', Hero(name='M', hp=12, max_hp=12))['id']
rows = [('x%03d'%i, 1000.0+i) for i in range(48)] + [('z%02d'%i, 7000.0) for i in range(10)]
ins(store, cid, rows)
camp = api.get('/api/campaigns/'+cid).json()
win = [r['id'] for r in camp['conversation']]
print()
print("B) window (58 rows total, cut at 50):", win[:6], "...", win[-6:], "len", len(win))
print("   cursor", camp['conversation_cursor'])
p = api.get('/api/campaigns/%s/conversation?before=%s' % (cid, json.dumps(camp['conversation_cursor']))).json()
ids = [r['id'] for r in p['conversation']]
print("   older page:", ids)
print("   DUPES =", [i for i in ids if i in win])
allids = set(win) | set(ids)
print("   LOST  =", sorted(set(r[0] for r in rows) - allids))

# C) insert a NEW turn while the reader pages (created = max, i.e. the real case)
app, store, api = fresh('c')
cid = store.create('K', Hero(name='M', hp=12, max_hp=12))['id']
ins(store, cid, [('h%03d'%i, 4000.0+i) for i in range(60)])
camp = api.get('/api/campaigns/'+cid).json()
tie = api.get('/api/campaigns/%s/conversation?before=%s&limit=5' % (cid, json.dumps(camp['conversation_cursor']))).json()
ins(store, cid, [('zzz-new', 5000.0)])       # newer than every old row
p2 = api.get('/api/campaigns/%s/conversation?before=%s&limit=10' % (cid, json.dumps(tie['conversation_cursor']))).json()
print()
print("C) new turn inserted during paging: page1", [r['id'] for r in tie['conversation']], "page2", [r['id'] for r in p2['conversation']])
print("   dupes:", [r['id'] for r in p2['conversation'] if r['id'] in set(x['id'] for x in tie['conversation'])])
