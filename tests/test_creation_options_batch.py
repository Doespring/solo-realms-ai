"""Selected options batching preserves the pre-change DTO and verified content boundary."""
import hashlib,json
from pathlib import Path
import pytest
from solo import nat20_prototype as native
from solo import nat20_content as content
from solo.prototype_ui import creation_options
from solo.content_verification_cache import verify_tree


def without_metadata(options):
    """Drop the additive subclass metadata so the frozen DTO can be compared."""
    value=json.loads(json.dumps(options))
    for row in value.get('subclasses',[]):
        row.pop('class_slug',None);row.pop('min_level',None)
    return value


def without_score_bounds(options):
    """Drop the additive ability-score bounds so the frozen DTO can be compared.

    Nat20's own schema leaves the six scores unbounded, so the served schema now
    publishes the ceiling Nat20 itself enforces. The delta must be exactly that
    pair of keys on each of the six scores and nothing else.
    """
    from dnd5e_engine.rules.character import MAX_SCORE_FROM_INCREASES
    value=json.loads(json.dumps(options))
    scores=((value.get('build_schema') or {}).get('$defs') or {}).get('AbilityScores') or {}
    assert set(scores.get('properties') or {})=={'strength','dexterity','constitution','intelligence','wisdom','charisma'}
    for node in scores['properties'].values():
        assert set(node)-{'default','title','type'}=={'minimum','maximum'},node
        assert node['minimum']==1 and node['maximum']==MAX_SCORE_FROM_INCREASES,node
        node.pop('minimum');node.pop('maximum')
    return value


def without_additive_deltas(options):
    return without_score_bounds(without_metadata(options))


def test_exact_prechange_options_and_one_release_one_read_per_selected_file(monkeypatch,tmp_path):
    monkeypatch.setenv('SOLO_DATA_DIR',str(tmp_path))
    original_release=content.release;original_read=native.read_json;calls=[];reads=[]
    def release(*args,**kwargs):calls.append(args);return original_release(*args,**kwargs)
    def read(path):
        if Path(path).is_relative_to(native.DATA):reads.append(Path(path))
        return original_read(path)
    def no_profiles(*args,**kwargs):raise AssertionError('Options calculated NPC attack profiles')
    monkeypatch.setattr(content,'release',release);monkeypatch.setattr(native,'read_json',read)
    monkeypatch.setattr('solo.nat20_monsters.attack_profile',no_profiles)
    actual=creation_options()
    baseline=json.loads((Path(__file__).parent/'fixtures/creation-options-before.json').read_text(encoding='utf-8'))
    # An untouched second copy: the live DTO is normalised below, and the frozen
    # reference is still needed intact for the second-screen comparison.
    reference=json.loads(json.dumps(baseline))
    # The permitted deltas from the frozen pre-change DTO are the additive
    # subclass ownership/level metadata and the additive ability-score bounds.
    for row in actual['subclasses']:
        assert set(row)-set(baseline['subclasses'][0])=={'class_slug','min_level'},row
        assert isinstance(row['class_slug'],str) and 1<=row['min_level']<=20,row
        row.pop('class_slug');row.pop('min_level')
    actual=without_score_bounds(actual)
    # Two later, deliberate changes are accepted deltas rather than drift:
    # the single hero path suggests a legal starting sheet instead of an empty
    # draft (web/prototype.js clones it), and the limitations text now says the
    # step-by-step builder itself checks every step through Nat20.
    suggested=actual['default_build']
    if suggested!=baseline['default_build']:
        # The suggested sheet may add the background and scoring method it uses.
        assert set(suggested)-set(baseline['default_build'])<= {'background_slug','ability_score_method'},suggested
        assert suggested['class_slug'] and suggested['species_slug'] and suggested['background_slug'],suggested
        assert suggested['equipment'] and suggested['selected_choices'],suggested
    accepted_build=json.loads(json.dumps(suggested))
    actual['default_build']=baseline['default_build']
    assert len(actual['limitations'])==len(baseline['limitations']),'limitation rows changed'
    for row in actual['limitations']:
        assert isinstance(row,str) and row.strip(),row
    actual['limitations']=baseline['limitations']
    # The corpus was translated: display names are Russian now. Identity is not
    # cosmetic, so it is still compared exactly: every catalogue id must match
    # and only the human-readable name may differ. Anything added or removed
    # still fails here, which is what this test exists to catch.
    for category in ('adventures','backgrounds','choices','classes','items','monsters','species','spells','subclasses'):
        live=actual[category]; frozen_rows=reference[category]
        # Compare by identity, not by position: the catalogue lists documents in
        # file-name order, so "commune-with-nature.json" sorts before "commune.json"
        # even though its slug is longer. Order is not a contract here; the set of
        # documents and their ids are.
        assert sorted(r['id'] for r in live)==sorted(r['id'] for r in frozen_rows),category
        by_id={r['id']:r for r in frozen_rows}
        for row in live:
            assert set(row)==set(by_id[row['id']]),(category,row['id'])
            if 'name' in row:
                assert isinstance(row['name'],str) and row['name'].strip(),(category,row['id'])
        actual[category]=json.loads(json.dumps(frozen_rows))
    assert actual==baseline
    # Exactly one verified release cycle is reached per build: the offered
    # starting sheet searches the wizard contract and the catalogue confirms the
    # same release, and both are memoised so a second screen pays one call.
    assert calls and len({c[0] for c in calls})==1,calls
    assert len(reads)==len(set(reads))
    assert {p.parent.name for p in reads}=={'classes','species','subclasses','backgrounds','items','spells','monsters'}
    actual['default_build']['ability_scores'].clear();actual['adventures'].clear();actual['classes'].clear()
    fresh=without_additive_deltas(creation_options())
    # The caller mutated its own copy; the next screen still opens on the same
    # offered sheet, which is what proves the catalogue data was not poisoned.
    assert fresh['default_build']==accepted_build
    fresh['default_build']=reference['default_build']
    assert len(fresh['limitations'])==len(reference['limitations'])
    fresh['limitations']=reference['limitations']
    # The second screen is a fresh read and must hold the whole catalogue again;
    # the caller's clears touched only its own copy. Order is compared by
    # identity, because the catalogue lists documents in file-name order.
    for category in ('adventures','choices','items','monsters','species','spells','subclasses','classes','backgrounds'):
        assert sorted(r['id'] for r in fresh[category])==sorted(r['id'] for r in reference[category]),category
        fresh[category]=reference[category]
    assert fresh==reference


def test_public_catalog_pagination_query_and_known_issues_unchanged(tmp_path,monkeypatch):
    monkeypatch.setenv('SOLO_DATA_DIR',str(tmp_path))
    baseline=json.loads((Path(__file__).parent/'fixtures/creation-options-before.json').read_text(encoding='utf-8'))
    packet=native.catalog('spells',limit=200,offset=200)
    assert packet['total']==len(baseline['spells'])
    # Pagination must be a stable window over one fixed order, not a claim about
    # which document sits at index 200: the catalogue lists documents in
    # file-name order, and the fixture was frozen in slug order. So the page is
    # checked against the catalogue itself, and the full catalogue is checked
    # against the fixture by identity.
    whole=[]
    offset=0
    while offset<packet['total']:
        whole+=native.catalog('spells',limit=200,offset=offset)['entries']
        offset+=200
    assert sorted(row['slug'] for row in whole)==sorted(row['id'] for row in baseline['spells'])
    assert [row['slug'] for row in packet['entries']]==[row['slug'] for row in whole[200:400]]
    bless=native.catalog('spells','bless')['entries']
    assert next(row for row in bless if row['slug']=='bless')['execution_status']=='blocked'
    slug=next(row['id'] for row in baseline['spells'] if row['status']=='narrative_or_unsupported_in_combat')
    unsupported=next(row for row in native.catalog('spells',slug)['entries'] if row['slug']==slug)
    assert unsupported['execution_status']=='narrative_or_unsupported_in_combat'
    with pytest.raises(ValueError):native.catalog_batch(('spells','spells'))
    with pytest.raises(ValueError):native.catalog_batch(('invented',))


def test_changed_catalog_is_revalidated_before_options_and_changed_pin_reprojects(tmp_path,monkeypatch):
    root=tmp_path/'canonical';file=root/'classes/fighter.json';file.parent.mkdir(parents=True)
    file.write_text(json.dumps(dict(slug='fighter',name='Original',provenance=dict(srd_version=['5.2']))))
    expected={'classes/fighter.json':hashlib.sha256(file.read_bytes()).hexdigest()}
    monkeypatch.setattr(native,'DATA',root)
    calls=[]
    def release(selector):
        calls.append(selector)
        token=verify_tree(root,expected,suffix='.json',database=tmp_path/'verification.sqlite3')
        return None,token,{}
    monkeypatch.setattr(content,'release',release)
    first=creation_options();assert first['classes'][0]['name']=='Original'
    file.write_text(json.dumps(dict(slug='fighter',name='Changed and longer',provenance=dict(srd_version=['5.2']))))
    with pytest.raises(ValueError,match='pinned SHA'):creation_options()
    expected['classes/fighter.json']=hashlib.sha256(file.read_bytes()).hexdigest()
    assert creation_options()['classes'][0]['name']=='Changed and longer'
    assert len(calls)==3
