
import os, sys, json, time, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests")
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import create_app
from test_dialogue_paging_api import MockLLM
tmp = Path(tempfile.mkdtemp())

def make(n, same_created=None, nid=None):
    app = create_app(tmp/('h%s.sqlite3'%time.time_ns()), MockLLM())
    st = app.state.store
    with st.connection(True) as c:
        c.execute("INSERT INTO campaigns(id,name,state,master,version,created) VALUES(?,?,?,?,?,?)",
                  ('c1','x',json.dumps({'scene':'двор','turn':n}),'{}',1,time.time()))
        for i in range(n):
            created = same_created if same_created is not None else 4000.0+i
            ident = ('h%03d'%i) if nid is None else nid(i)
            c.execute("INSERT INTO conversations(id,campaign,request_id,original,reply,kind,context,created) VALUES(?,?,?,?,?,?,?,?)",
                      (ident,'c1','rq%s'%ident,'t%d'%i,'a%d'%i,'turn','{}',created))
    return TestClient(app)

def walk(api, limit):
    seen, cursor, guard = [], None, 0
    while guard < 60:
        guard += 1
        params={'limit':limit}
        if cursor is not None: params['before']=json.dumps(cursor)
        page=api.get('/api/campaigns/c1/conversation', params=params).json()
        seen += [r['id'] for r in page['conversation']]
        if not page['conversation_more'] or not page['conversation_cursor']: break
        cursor=page['conversation_cursor']
    return seen

# A. all rows same created, random-ish ids
api = make(10, same_created=4000.0, nid=lambda i: 'z%02d'%((i*7)%10))
s = walk(api, 3)
print("A same-created walk:", s, "unique", len(set(s)), "total", len(s))

# B. same created, ids that sort inconsistently with insertion
api = make(10, same_created=4000.0, nid=lambda i: ['m','a','z','b','k','c','y','d','x','e'][i])
s = walk(api, 3)
print("B same-created irregular ids:", s, "unique", len(set(s)), "total", len(s))

# C. duplicate id impossible (PK). But equal created + id collation? ids are text: 'h10' < 'h9' lexicographic
api = make(12, same_created=4000.0)
s = walk(api, 5)
print("C same-created h0..h11:", s, "unique", len(set(s)), "total", len(s))

# D. shift during paging: new turns appended between pages
api = make(10)
st = None
p1 = api.get('/api/campaigns/c1/conversation', params={'limit':4}).json()
print("D page1:", [r['id'] for r in p1['conversation']], "more", p1['conversation_more'], "cursor", p1['conversation_cursor'])
# now insert a NEW turn with created BETWEEN cursor and rest (e.g. same created as cursor, smaller id -> appears after cursor in desc order? )
