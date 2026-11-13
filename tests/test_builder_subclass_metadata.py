"""Builder metadata must come from the pinned corpus, not from client guesses.

The ordinary builder filters subclasses by (class_slug, min_level). Those two
values must equal what the native build validator actually enforces, otherwise
the UI would offer a choice that the engine rejects (or hide a legal one).
"""
import pytest
from solo import nat20_prototype as native
from solo.prototype_ui import creation_options


def test_subclass_metadata_matches_native_gate_for_every_pinned_record():
    from dnd5e_engine.rules.character import subclass_gate_level

    packet=native.catalog_batch(('subclasses',))
    assert packet['entries'],'pinned corpus has no subclass records'
    for row in packet['entries']:
        assert set(row)>={'class_slug','min_level'},row
        assert row['class_slug'] and isinstance(row['min_level'],int),row
        # The record's own ownership and its resulting class gate.
        record=native.catalog_detail(row['id'])['data']
        assert row['class_slug']==record['class_identifier']
        from dnd5e_srd_data.loader import BundledAssetLoader
        loader=BundledAssetLoader(root=native.DATA)
        gate=subclass_gate_level(loader.get_class(record['class_identifier']))
        assert row['min_level']==gate,\
            f"{row['slug']}: projection says {row['min_level']}, native gate is {gate}"


def test_options_expose_only_real_class_slugs_and_levels():
    options=creation_options()
    classes={row['id'] for row in options['classes']}
    assert options['subclasses'],'builder received no subclass rows'
    for row in options['subclasses']:
        assert row['class_slug'] in classes,row
        assert 1<=row['min_level']<=20,row
        assert row['id'] not in classes


def test_non_subclass_rows_keep_the_previous_shape():
    options=creation_options()
    for category in ('classes','species','backgrounds','items','spells','monsters'):
        for row in options[category]:
            assert set(row)=={'id','catalogue_id','name','issue','status'},(category,row)


def test_every_class_has_a_reachable_subclass_and_every_subclass_a_class():
    options=creation_options()
    owned={row['class_slug'] for row in options['subclasses']}
    assert owned=={row['id'] for row in options['classes']},'class/subclass graph is incomplete'
    for row in options['subclasses']:
        assert row['min_level']<=20
