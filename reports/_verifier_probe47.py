import os, sys, json, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r'C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI')
sys.path.insert(0, r'C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests')
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.storage import Store
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from test_nat20_command_cycle import ACTIONS

tmp = Path(tempfile.mkdtemp())
store = Store(tmp/'c.sqlite3')
cid = Nat20GameEngine(store).create_campaign('HTTP fighter', seed=5)['id']
llm = CommandMockLLM({k: dict(status='command', action=v, message='') for k, v in ACTIONS.items()})
api = TestClient(create_app(store.path, llm))
r = api.post('/api/campaigns/%s/command-turn' % cid, json=dict(request_id='r1', text='check', expected_version=store.campaign(cid)['version']))
print('command-turn:', r.status_code, r.json().get('status'), '| journal_id:', r.json().get('journal_id'))
with store.connection() as c:
    rows = [dict(x) for x in c.execute('SELECT id,request_id,kind,context FROM conversations WHERE campaign=?', (cid,)).fetchall()]
print('conversation rows:', len(rows))
for row in rows:
    ctx = row['context']
    try: ctx = json.loads(ctx) if isinstance(ctx, str) else ctx
    except Exception: pass
    print('   ', row['request_id'], row['kind'], '| context keys:', sorted(ctx.keys()) if isinstance(ctx, dict) else ctx)
print()
camp = api.get('/api/campaigns/%s' % cid).json()
print('campaign conversation journal_event:', [(x['id'], x.get('journal_event')) for x in camp.get('conversation', [])])
page = api.get('/api/campaigns/%s/conversation' % cid).json()
print('paged conversation journal_event:', [(x['id'], x.get('journal_event')) for x in page.get('conversation', [])])
print('events in journal:', len(store.history(cid, 500)))