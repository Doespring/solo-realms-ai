"""Offline isolated pending content release. Does not rewrite the fighter manifest."""
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

from import_nat20_minimal import read_pack, translator, encode

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from solo.nat20_bless import ROOT, RELEASE, normalize_bless
from solo.nat20_content import release

SELECT = {
    'spells24': {'phbsplBless00000': ('spells', 'bless', translator.translate_spell_yaml)},
    'classes24': {
        'phbclcCleric0000': ('classes', 'cleric', translator.translate_class_yaml),
        'phbclcSpellcasti': ('features', 'spellcasting-cleric', translator.translate_feature_yaml),
        'phbclcDivineOrde': ('features', 'divine-order', translator.translate_feature_yaml),
        'phbDivineOrderPr': ('features', 'protector', translator.translate_feature_yaml)},
    'equipment24': {'phbwepMace000000': ('items', 'mace', translator.translate_weapon_yaml),
                    'phbhsyAmuletworn': ('items', 'amulet', translator.translate_generic_item_yaml)}}


def main():
    records = []
    source = BASE / 'integration/nat20/sources/foundry-5.3.3/dnd5e/packs'
    entries = []
    for pack, selection in SELECT.items():
        documents = read_pack(source / pack)
        for identity, (category, slug, function) in selection.items():
            document = documents[identity]
            if document['system']['source'].get('rules') != '2024' or document['system']['source'].get('license') != 'CC-BY-4.0':
                raise ValueError('Unreviewed source provenance')
            raw_path = ROOT / 'raw/_source' / pack / (slug + '.yml')
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(encode(document), encoding='utf-8')
            projected = normalize_bless(document) if slug == 'bless' else document
            # Slugs are explicit import identities; preserve original document in raw.
            projected = json.loads(json.dumps(projected))
            projected['system']['identifier'] = slug
            projection = ROOT / 'projection/_source' / pack / (slug + '.yml')
            projection.parent.mkdir(parents=True, exist_ok=True)
            projection.write_text(encode(projected), encoding='utf-8')
            entries.append((pack, identity, category, slug, function, raw_path, projection))
    index = translator.build_feature_index(ROOT / 'projection/_source')
    for pack, identity, category, slug, function, raw_path, projection in entries:
        options = dict(ingest_date=date(2026, 10, 18), ingest_version='solo-bless-pending-v1')
        import inspect
        if 'feature_index' in inspect.signature(function).parameters: options['feature_index'] = index
        result = function(projection, **options).model_dump(mode='json')
        if category == 'classes':
            result['granted_features'] = [{**index[item['uuid']].model_dump(mode='json'), 'level': a.get('level', 0)}
                for a in result['advancement'] if a['type'] == 'ItemGrant' and a.get('level', 0) <= 1
                for item in a['configuration'].get('items', [])]
        result['provenance']['source_url'] = f'https://github.com/foundryvtt/dnd5e/releases/tag/release-5.3.3#Compendium.dnd5e.{pack}.Item.{identity}'
        target = ROOT / 'canonical' / category / (slug + '.json')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encode(result), encoding='utf-8')
        records.append(dict(category=category,slug=slug,file=target.relative_to(ROOT).as_posix(),sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            raw_file=raw_path.relative_to(ROOT).as_posix(),raw_sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            projection_file=projection.relative_to(ROOT).as_posix(),projection_sha256=hashlib.sha256(projection.read_bytes()).hexdigest(),
            foundry_pack=pack,foundry_id=identity,rules_version='dnd2024',locale='en',license='CC-BY-4.0'))
    manifest=dict(release_id=RELEASE,rules_version='dnd2024',status='pending',parent_release_digest=release()[1],
        parent_manifest_sha256=hashlib.sha256((BASE/'integration/nat20/imported/manifest.json').read_bytes()).hexdigest(),
        normalization='solo.nat20_bless.normalize_bless v1: reviewed add -> mode 2; raw immutable',documents=records)
    (ROOT/'manifest.json').write_text(encode(manifest),encoding='utf-8')
    print(json.dumps(dict(release_id=RELEASE,status='pending',documents=len(records))))


if __name__ == '__main__': main()
