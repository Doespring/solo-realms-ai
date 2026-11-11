
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
app = create_app(tmp/'j.sqlite3', MockLLM())
store = app.state.store
api = TestClient(app)
a = store.create('A', Hero(name='A', hp=12, max_hp=12))['id']
b = store.create('B', Hero(name='B', hp=12, max_hp=12))['id']

def act(cid, aid):
    with store.connection(True) as c:
        c.execute("INSERT INTO actions(id,campaign,request_id,original,plan,status,version,created) VALUES(?,?,?,?,?,?,?,?)",
                  (aid, cid, 'q'+aid, 'Ход', '{}', 'resolved', 1, 1000.0))

def ev(cid, eid, seq):
    act(cid, 'a'+eid)
    with store.connection(True) as c:
        c.execute("INSERT INTO events(id,seq,action,campaign,before_state,after_state,results,narrative,narrative_status,error,undone,created,narrative_data) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (eid, seq, 'a'+eid, cid, '{}','{}','[]','', 'complete','',0,1000.0,'{}'))

def conv(cid, rid, jid):
    with store.connection(True) as c:
        c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                  (rid, cid, 'q'+rid, 'Ход', 'Ответ', 'turn', json.dumps({'journal_id': jid}), 1000.0))

ev(a, 'evA', 1); ev(b, 'evB', 2)
# campaign A row points at campaign B's event
conv(a, 'rA1', 'evB')
# and a row pointing at a non-existent event
conv(a, 'rA2', 'nope')
page = api.get('/api/campaigns/%s/conversation' % a).json()['conversation']
print("A page rows:", [(r['id'], r.get('journal_event')) for r in page])
print("   -> cross-campaign event 'evB' leaked into A's page?", any(r.get('journal_event')=='evB' for r in page))
print()

# 500-event bound: a row whose event falls outside the newest 500
with store.connection(True) as c:
    c.execute("DELETE FROM conversations")
    c.execute("DELETE FROM events")
for i in range(1, 601):
    ev(a, 'e%04d' % i, 100 + i)
conv(a, 'rOld', 'e0001')   # oldest event, outside history(campaign, 500)
conv(a, 'rNew', 'e0600')
page = api.get('/api/campaigns/%s/conversation' % a, params={'limit': 200}).json()['conversation']
m = {r['id']: r.get('journal_event') for r in page}
print("journal size:", len(store.history(a, 10000)), "| what history(a,500) sees:", len(store.history(a, 500)))
print("row bound to the OLDEST event  -> journal_event:", m.get('rOld'), "(None = link dropped)")
print("row bound to the NEWEST event  -> journal_event:", m.get('rNew'))
print("   -> an old turn loses its link once >500 events exist:", m.get('rOld') is None and m.get('rNew') == 'e0600')
print()
# does the campaign response use the same 500 window?
camp = api.get('/api/campaigns/%s' % a).json()
cm = {r['id']: r.get('journal_event') for r in camp.get('conversation', [])}
print("campaign response rOld:", cm.get('rOld'), "| rNew:", cm.get('rNew'))
print("context leaked in paged rows?", [k for r in page for k in r if k == 'context'])