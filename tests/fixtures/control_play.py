"""Play the control book: open the entry, put a legal hero in it, walk and end."""
import json, os, sys
from pathlib import Path

os.environ.setdefault('SOLO_TEST', '1')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.cloud import CloudLLM
from solo.cloud_settings import CloudSettings
from solo.cloud_cost import CostLedger
from solo.prototype_prepared import prepared_start_readiness

WORK = ROOT / '.uitmp' / 'control'
PROFILE_DIR = WORK / 'profile'


def main():
    llm = CloudLLM(CloudSettings(PROFILE_DIR / 'cloud-profiles.json'),
                   CostLedger(PROFILE_DIR / 'cloud-usage.sqlite3'))
    app = create_app(WORK / 'solo.sqlite3', llm, experimental=True)
    client = TestClient(app)
    client.__enter__()
    try:
        did = (WORK / 'draft-id.txt').read_text(encoding='utf-8').strip()
        pipeline = app.state.preparation_pipeline
        private = app.state.preparation_pipeline.raw(
            app.state.preparation_pipeline.latest(did)['id'])
        run_id = private['id']
        print('run', run_id, 'status', private['status'])
        readiness = prepared_start_readiness(app.state.preparation, did)
        print('readiness before', json.dumps({k: readiness.get(k) for k in ('ready','world_ready','revision')}, ensure_ascii=False))
        if not readiness.get('world_ready'):
            raise SystemExit('world not ready: ' + json.dumps(readiness, ensure_ascii=False)[:400])
        pipeline.confirm_world(did, run_id=run_id, expected_revision=readiness['revision'], confirm=True)
        after = prepared_start_readiness(app.state.preparation, did)
        print('readiness after', json.dumps({k: after.get(k) for k in ('ready','world_ready')}, ensure_ascii=False))

        from nat20_builds import legal
        body = dict(name='Рив', build=legal(class_slug='rogue', species_slug='human', level=1,
                                           equipment=['dagger', 'potion-of-healing']),
                    prepared_spells=[], adventure='prepared:' + did, description='',
                    encounter_monsters=[], mechanics_ruleset='dnd2024', rules_confirmed=True,
                    hero_confirmed=True, background_confirmed=True,
                    adventure_revision=app.state.preparation.get(did)['revision'],
                    request_id='control-start')
        created = client.post('/api/prototype/campaigns', json=body)
        print('create', created.status_code, created.text[:300])
        if created.status_code != 200:
            raise SystemExit(1)
        campaign = created.json()['id']
        state = client.get('/api/campaigns/' + campaign).json()['state']
        print('location', state['location'])
        print('locations', [p['name'] for p in state['locations']])
        print('exits', json.dumps(state.get('routes') or state.get('transitions') or {}, ensure_ascii=False)[:400])
        print('hero', state.get('hero', {}).get('name'), state.get('hero', {}).get('class_name'))
        Path(WORK / 'game-state.json').write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
        Path(WORK / 'campaign-id.txt').write_text(campaign, encoding='utf-8')
    finally:
        client.__exit__(None, None, None)


if __name__ == '__main__':
    main()
