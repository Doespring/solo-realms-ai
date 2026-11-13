import os, sys
sys.path.insert(0, '/home/hermes/projects/SoloRealmsAI-Nat20')
os.environ['SOLO_TEST'] = '1'
from solo.nat20_wizard import WizardDraft, contract, level_up_draft, suggested_draft

for klass in ('wizard', 'cleric'):
    draft, _final = suggested_draft(class_slug=klass, species_slug='human',
                                    background_slug='soldier', level=7)
    before = contract(draft)
    magic_before = next((s for s in before['steps'] if s['id'] == 'magic'), None)
    advanced = level_up_draft(WizardDraft.model_validate(
        draft.model_dump(mode='json') | {'advancement': True}))
    after = contract(advanced['draft'])
    magic = next((s for s in after['steps'] if s['id'] == 'magic'), None)
    owed = [(s['id'], s.get('status'), s.get('missing')) for s in after['steps']
            if s['id'] not in ('species', 'lineage', 'class', 'background', 'scores', 'equipment', 'final')
            and s.get('required') and s.get('status') != 'complete']
    print(klass, advanced['from_level'], '->', advanced['to_level'],
          'slots', advanced['granted_slots'],
          'magic before', (magic_before or {}).get('status'), (magic_before or {}).get('missing'),
          'after', (magic or {}).get('status'), (magic or {}).get('missing'),
          'owed', owed)
