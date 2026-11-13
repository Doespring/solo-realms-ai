"""One pending Bless/self release. No spell mathematics or vendor modifications."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from .game_commands import bless_stand_enabled

from dnd5e_srd_data.schema.class_ import Class
from dnd5e_srd_data.schema.feature import Feature
from dnd5e_srd_data.schema.item import Item, Weapon
from dnd5e_srd_data.schema.spell import Spell

ROOT = Path(__file__).resolve().parents[1] / 'integration/nat20/releases/bless-self-2024-v1'
RELEASE = 'bless-self-2024-v1'
PROFILE = 'cleric-bless-2024-v1'
BONUS_KEYS = {'system.bonuses.abilities.save', 'system.bonuses.mwak.attack',
              'system.bonuses.msak.attack', 'system.bonuses.rsak.attack', 'system.bonuses.rwak.attack'}
BLOCKERS = ['post-combat concentration elapsed-time API not bound',
            'upstream concentration save does not fold Bless saving-throw bonus',
            'upstream same-identity recast duplicates effects and drop leaves a copy',
            'upstream standalone saving throw does not alias Foundry Bless save keys',
            'complete Cleric preparation/cantrip and origin choices not certified']


def require_stand():
    if not bless_stand_enabled():
        raise ValueError('Bless profile activation pending: ' + '; '.join(BLOCKERS))


def normalize_bless(document):
    """Reviewed Foundry 5.3.3 data-shape bridge; preserve raw source separately."""
    value = deepcopy(document)
    if value.get('_id') != 'phbsplBless00000' or value.get('system', {}).get('source', {}).get('rules') != '2024':
        raise ValueError('Unreviewed Bless source/edition')
    effects = value.get('effects', [])
    if len(effects) != 1 or effects[0].get('_id') != '1dzvDffEh8wZ0oQy':
        raise ValueError('Missing reviewed Bless effect')
    effect = effects[0]
    if effect.get('changes'):
        raise ValueError('Ambiguous mixed effect formats')
    changes = effect.get('system', {}).get('changes', [])
    if len(changes) != len(BONUS_KEYS) or {c.get('key') for c in changes} != BONUS_KEYS:
        raise ValueError('Empty, duplicate or unsupported Bless bonus')
    if any(c.get('type') != 'add' or c.get('value') != '1d4' for c in changes):
        raise ValueError('Unsupported effect operation/formula')
    effect['changes'] = [dict(key=c['key'], mode=2, value=c['value']) for c in changes]
    return value


def extension(documents, base_fingerprint):
    require_stand()
    from .nat20_content import digest
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest['release_id'], manifest['rules_version'], manifest['parent_release_digest']) != (RELEASE, 'dnd2024', base_fingerprint):
        raise ValueError('Bless content parent/edition mismatch; no implicit migration')
    models = {'classes': Class, 'features': Feature, 'spells': Spell, 'items': Item}
    documents = {k: list(v) for k, v in documents.items()}
    documents['spells'] = []
    for record in manifest['documents']:
        for field, sha in [('file', 'sha256'), ('raw_file', 'raw_sha256'), ('projection_file', 'projection_sha256')]:
            path = (ROOT / record[field]).resolve()
            if not path.is_relative_to(ROOT.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != record[sha]:
                raise ValueError('Bless source/projection hash mismatch')
        if record['rules_version'] != 'dnd2024' or record['license'] != 'CC-BY-4.0':
            raise ValueError('Bless provenance gate failed')
        body = json.loads((ROOT / record['file']).read_text(encoding='utf-8'))
        category = record['category']
        model = Weapon if category == 'items' and body.get('item_kind') == 'weapon' else models[category]
        if body['slug'] != record['slug'] or any(d.slug == body['slug'] for d in documents.setdefault(category, [])):
            raise ValueError('Bless duplicate or unexpected identity')
        documents[category].append(model.model_validate(body))
    spell = documents['spells'][0]
    changes = [c for e in spell.passive_effects for c in e.changes]
    if (spell.slug != 'bless' or not spell.concentration or len(changes) != 5
            or {c.key for c in changes} != BONUS_KEYS or any(c.mode != 2 or c.value != '1d4' for c in changes)
            or len(spell.activities) != 1 or len(spell.activities[0].effects) != 1
            or spell.activities[0].effects[0].id != spell.passive_effects[0].id):
        raise ValueError('Bless has no verified executable bonus')
    return documents, digest({'parent_release_digest': base_fingerprint, 'extension': manifest})


def component_gate(state, loader):
    """Narrow fixed-loadout eligibility, never confirmation from AI/prose.

    This stand has one one-handed mace, no shield, a held nonconsumed Amulet,
    bright open terrain and no environmental silence handler. Wider cases reject.
    """
    member = state['hero']
    if member.get('class_slug') != 'cleric' or set(member.get('equipment', [])) != {'mace', 'amulet'}:
        raise ValueError('Unreviewed caster hands/components/loadout')
    symbol = loader.get_item('amulet')
    if symbol is None or symbol.cost_gp is None or symbol.cost_gp < 5:
        raise ValueError('Required Holy Symbol worth 5 GP is absent')
    if state['active_effects']:
        # Existing own Bless is the only supported effect, permitting replacement.
        if any(e.get('statuses') or e.get('origin') != 'cast:blessed:hero' for e in state['active_effects']):
            raise ValueError('Verbal/somatic eligibility under this effect is unbound')
