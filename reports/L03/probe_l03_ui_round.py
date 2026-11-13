import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, '/home/hermes/projects/SoloRealmsAI-Nat20')
sys.path.insert(0, '/home/hermes/projects/SoloRealmsAI-Nat20/tests')
os.environ['SOLO_TEST'] = '1'
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.preview_mock import PreviewMock
from nat20_builds import legal

work = Path(tempfile.mkdtemp(prefix='l03c-', dir='/home/hermes/.hermes/kanban/workspaces/t_d6d42e2c'))
app = create_app(work / 'game.sqlite3', PreviewMock(), experimental=True, offline_mock=True)
build = legal(class_slug='fighter', species_slug='human', background='soldier',
              subclass_slug='champion', level=7, equipment=['longsword', 'chain-mail', 'shield'])
with TestClient(app) as api:
    created = api.post('/api/prototype/campaigns', json=dict(
        name='Арден', request_id='create-1', adventure='authored', description='Пустой погреб.',
        build=build, prepared_spells=[], hero_background='Служил в городской страже.',
        encounter_monsters=['rat'], encounter_attack_bonuses={'rat': 0},
        encounter_attack_sources={'rat': 'Authored smoke encounter: explicit +0 selected by test author.'}))
    assert created.status_code == 200, created.text
    cid = created.json()['id']
current = app.state.store.campaign(cid)
runtime = current['state']['nat20']
reward = runtime['definition']['encounters']['opening']['reward']
runtime['experience'] = 34000
runtime['rewards'] = [dict(id=reward['id'], encounter_id='opening', combat_id='opening', experience=34000,
                           outcome_sha256='0' * 64, source=reward['source'])]
with app.state.store.connection(True) as db:
    db.execute('UPDATE campaigns SET state=?, version=version+1 WHERE id=?',
               (json.dumps(current['state'], ensure_ascii=False), cid))

def owed(contract):
    return [(s['id'], s.get('choice_key'), s.get('status'), s.get('missing')) for s in contract['steps']
            if s['id'] not in ('species','lineage','class','background','scores','equipment','final')
            and s.get('required') and s.get('status') != 'complete']

with TestClient(app) as api:
    state = api.get('/api/campaigns/' + cid + '/level-up').json()
    draft = state['draft']
    answers = {}
    print('GET  from', state['from_level'], 'to', state['to_level'],
          'choices', len(draft['selected_choices']), 'owed', owed(state['contract']))

    def contract_round(answers, draft):
        answer = api.post('/api/prototype/wizard/contract', json=dict(draft=draft, answers=answers))
        assert answer.status_code == 200, answer.text
        body = answer.json()
        print('  contract: choices', len(body['draft']['selected_choices']),
              'blocking', body['blocking'], 'owed', owed(body),
              'issues', body['issues'][:2], 'answers', list(body['answers']))
        return body

    body = contract_round({}, draft)
    draft = body['draft']
    body = contract_round({'class:fighter:asi:8:Ability Score Improvement': {'dexterity': 1}}, draft)
    draft = body['draft']
    body = contract_round({'class:fighter:asi:8:Ability Score Improvement': {'dexterity': 2}}, draft)
    draft = body['draft']
    print('  final draft choices', draft['selected_choices'])
    done = api.post('/api/campaigns/' + cid + '/level-up', json=dict(
        request_id='level-8', expected_version=state['version'], draft=draft,
        answers={'class:fighter:asi:8:Ability Score Improvement': {'dexterity': 2}}))
    print('COMMIT', done.status_code, done.text[:300])
    after = app.state.store.campaign(cid)['state']
    print('level', after['nat20']['definition']['build']['level'],
          'dexterity', after['nat20']['sheet']['ability_scores'],
          'background', after['hero']['background'],
          'equipment', after['nat20']['definition']['build']['equipment'])
