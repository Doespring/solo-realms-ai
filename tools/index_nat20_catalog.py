"""Offline metadata search index. Never imports mechanics or executes pack code."""
import hashlib
import json
from pathlib import Path

from dfindexeddb.leveldb.record import FolderReader

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'integration/nat20/sources/foundry-5.3.3/dnd5e'
OUTPUT = ROOT / 'integration/nat20/playable/search-catalog.json'


def build_index():
    inventory = json.loads((ROOT / 'integration/nat20/pack-inventory.json').read_text(encoding='utf-8'))
    packs, documents = [], []
    for pack in inventory['packs']:
        folder = SOURCE / 'packs' / pack['pack']
        if not pack['id'].startswith('foundry.dnd5e.') or not folder.is_dir():
            continue
        packs.append({k: pack[k] for k in ('id', 'source', 'rules_version', 'locale', 'license', 'provenance')})
        active = {}
        for wrapper in FolderReader(folder).GetRecords(use_manifest=True):
            if wrapper.recovered:
                continue
            record = wrapper.record
            old = active.get(record.key)
            if old is None or record.sequence_number > old.sequence_number:
                active[record.key] = record
        for key, record in sorted(active.items()):
            if int(record.record_type) != 1 or not key.startswith(b'!'):
                continue
            parts = key.decode().split('!')
            if len(parts) != 3 or '.' in parts[1] or '.' in parts[2]:
                continue  # embedded items/effects/pages are not catalogue entries
            doc = json.loads(record.value)
            if not isinstance(doc, dict) or doc.get('_id') != parts[2]:
                raise ValueError('Invalid top-level Foundry identity')
            source = doc.get('system', {}).get('source', {})
            edition = {'2024': 'dnd2024', '2014': 'dnd2014'}.get(source.get('rules'), pack['rules_version'])
            documents.append(dict(pack=pack['id'], id=doc['_id'], name=doc.get('name', ''),
                type=doc.get('type'), rules_version=edition, locale=pack['locale'],
                license=source.get('license'), sha256=hashlib.sha256(record.value).hexdigest(),
                import_status='metadata_only', executable=False))
    return dict(schema_version='source-search-catalog-v1',
                scope='All top-level documents in locally extracted Foundry packs; other packs remain manifest-only in pack-inventory.json',
                execution_allowlist='capabilities.json', packs=packs, documents=documents)


if __name__ == '__main__':
    result = build_index()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(json.dumps(dict(packs=len(result['packs']), documents=len(result['documents']))))
