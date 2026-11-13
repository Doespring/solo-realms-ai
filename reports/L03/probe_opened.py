import json, sys
sys.path.insert(0, '/home/hermes/projects/SoloRealmsAI-Nat20')
sys.path.insert(0, '/home/hermes/projects/SoloRealmsAI-Nat20/tests')
from nat20_builds import legal
from solo.nat20_wizard import WizardDraft, contract, level_up_draft

for cls in ('wizard', 'fighter', 'cleric', 'rogue'):
    for level in (1, 4, 5, 6, 7, 8, 9):
        build = legal(class_slug=cls, species_slug='human', background='soldier', level=level)
        draft = WizardDraft(advancement=True, name='Арден', backstory='', level=level, **{k: v for k, v in build.items() if k in (
            'species_slug','class_slug','subclass_slug','background_slug','ability_scores','ability_score_method','selected_choices','prepared_spells','equipment')})
        try:
            adv = level_up_draft(draft)
        except Exception as exc:
            print(cls, level, 'ERR', type(exc).__name__, exc)
            continue
        c = contract(adv['draft'])
        print('===', cls, adv['from_level'], '->', adv['to_level'], 'opened=', adv['opened'],
              'pending=', [(r['key'], r['before'], r['after']) for r in adv['pending']])
        print('   steps:', [(s['id'], s.get('choice_key'), s.get('missing'), s.get('required'), s.get('status')) for s in c['steps']])
        print('   blocking:', c['blocking'], 'ready=', c['ready'], 'issues=', c['issues'][:3])
        print('   granted slots=', adv['granted_slots'], 'feats=', [r['id'] for r in adv['granted_feats']],
              'features=', [r['id'] for r in adv['granted_features']])
