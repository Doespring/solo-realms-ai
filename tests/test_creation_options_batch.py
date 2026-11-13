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


def without_schema_additions(options):
    """Drop the two later schema additions so the frozen DTO can be compared.

    Both are engine facts the frozen fixture predates, not drift:

    - `item_variants` publishes the variant switch an item offers (Adamantine
      armour, weapon variants) so the builder can ask for it instead of guessing.
    - `ac_calc_mode` gained `custom` because Foundry items such as the Robe of
      the Archmagi carry their own AC formula; build_spec reads it from the item.

    Each is checked to be exactly the shape described here and nothing else.
    """
    value=json.loads(json.dumps(options))
    properties=value['build_schema']['properties']
    variants=properties.pop('item_variants')
    assert set(variants)=={'additionalProperties','title','type'},variants
    assert variants['type']=='object' and variants['additionalProperties']=={'type':'string'},variants
    mode=properties['ac_calc_mode']['anyOf'][0]['enum']
    assert mode==['default','mage_armor','unarmored_barbarian','unarmored_monk','unarmored_bard','custom'],mode
    properties['ac_calc_mode']['anyOf'][0]['enum']=mode[:-1]
    # Three fields the build contract gained after the fixture was frozen. Each
    # is asserted to be exactly the shape named here and nothing else, so an
    # unexplained addition still fails the comparison.
    #
    # - `starting_class` names which class supplies the starting proficiencies
    #   (§5 multiclassing): without it the verdict depends on dict order.
    # - `require_complete_choices` asks the engine to refuse an under-filled
    #   choice pool instead of silently producing a half-built sheet.
    # - `rules_edition` states which printed edition the choices were read for.
    flag=properties.pop('require_complete_choices')
    assert flag=={'default':False,'title':'Require Complete Choices','type':'boolean'},flag
    edition=properties.pop('rules_edition')
    assert edition=={'default':'5.2','title':'Rules Edition','type':'string'},edition
    starting=properties.pop('starting_class')
    assert starting=={'anyOf':[{'type':'string'},{'type':'null'}],'default':None,'title':'Starting Class'},starting
    return value


#: The conjuration spells whose catalogue status moved after the fixture was
#: frozen. Each one is in `dnd5e_engine.activities.conjuration.CONJURATION_ALLOWLIST`
#: and is driven end-to-end by tests/test_nat20_animate_dead.py and
#: tests/test_nat20_find_familiar*.py (18 passed); `solo/nat20_worker.py:162`
#: gates a cast on the SAME `spell_execution_status` the catalogue publishes, so
#: the screen and the executor can no longer disagree about them. Their status is
#: therefore asserted positively by
#: `test_conjuration_status_matches_what_the_executor_accepts` below instead of
#: being frozen here.
CONJURATION_GAINED = {'animate-dead','create-undead','find-familiar','find-steed','giant-insect'}


def without_conjuration_status(options):
    """Drop the status/issue pair of the five spells the engine gained.

    Everything else about those rows stays frozen and comparable.
    """
    value=json.loads(json.dumps(options))
    for row in value.get('spells',[]):
        if row.get('id') in CONJURATION_GAINED:
            row.pop('status',None);row.pop('issue',None)
    return value


def without_additive_deltas(options):
    return without_schema_additions(without_score_bounds(without_metadata(options)))


def test_exact_prechange_options_and_one_release_one_read_per_selected_file(monkeypatch,tmp_path):
    """Снимок DTO до изменения, кроме одного известного расхождения.

    Расхождение (не карточки, а кода): `_catalog_entries` помечает
    `native_candidate` любую заклинательную активность, в том числе summon,
    который обработчик не маршрутизирует (CONJURATION_ALLOWLIST в движке его
    не содержит): Animate Dead, Create Undead, Find Familiar, Find Steed,
    Giant Insect. `solo/nat20_worker.py` при `cast_spell` отвечает
    `narrative_or_unsupported_in_combat`, поэтому экран обещает механику,
    которой нет. То же расхождение снимается и в `catalog()`.

    Тест падал и ДО этого разбора (`assert actual==baseline`), то есть
    фиксировал старое поведение каталога. Чинить надо код каталога, а не
    снимок: снимок — эталон старого контракта, и подмена в нём скрыла бы
    реальную ошибку. Карточка: см. reports/ENGINE_WORKSPACE_PROGRESS.md, r125.
    """
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
    # The permitted deltas from the frozen pre-change DTO are the additive
    # subclass ownership/level metadata and the additive ability-score bounds.
    for row in actual['subclasses']:
        assert set(row)-set(baseline['subclasses'][0])=={'class_slug','min_level'},row
        assert isinstance(row['class_slug'],str) and 1<=row['min_level']<=20,row
        row.pop('class_slug');row.pop('min_level')
    actual=without_score_bounds(actual)
    actual=without_schema_additions(actual)
    baseline=without_conjuration_status(baseline)
    actual=without_conjuration_status(actual)
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
    fresh['default_build']=baseline['default_build']
    assert len(fresh['limitations'])==len(baseline['limitations'])
    fresh['limitations']=baseline['limitations']
    assert without_conjuration_status(fresh)==baseline


def test_public_catalog_pagination_query_and_known_issues_unchanged(tmp_path,monkeypatch):
    monkeypatch.setenv('SOLO_DATA_DIR',str(tmp_path))
    baseline=json.loads((Path(__file__).parent/'fixtures/creation-options-before.json').read_text(encoding='utf-8'))
    packet=native.catalog('spells',limit=200,offset=200)
    assert packet['total']==len(baseline['spells'])
    assert [row['slug'] for row in packet['entries']]==[row['id'] for row in baseline['spells'][200:400]]
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

def test_published_spell_status_is_exactly_what_the_executor_gates_on():
    """The screen and the executor must read the same truth.

    `solo/nat20_worker.py:162` refuses a cast when `spell_execution_status`
    says `narrative_or_unsupported_in_combat`. The catalogue may therefore not
    publish a different status for the same file: offering `native_candidate`
    for a spell the executor refuses promises the player a mechanic that is not
    there, and the reverse hides one that is.

    This is the guard for that whole class: one fact, one decider. The frozen
    DTO below stops freezing status for the five conjuration spells the engine
    gained after it was captured, and this test states their truth positively
    instead.
    """
    from solo.nat20_prototype import spell_execution_status
    offered={row['id']:row['status'] for row in creation_options()['spells']}
    checked=0
    for path in sorted((native.DATA/'spells').glob('*.json')):
        raw=native.read_json(path);slug=raw['slug']
        if slug not in offered or offered[slug]=='blocked':
            continue
        expected=spell_execution_status(raw)
        assert offered[slug]==expected,(slug,offered[slug],expected)
        checked+=1
    assert checked>=300,checked
    from dnd5e_engine.activities.conjuration import CONJURATION_ALLOWLIST
    for slug in CONJURATION_GAINED:
        assert offered[slug]=='native_candidate',(slug,offered[slug])
        assert slug in CONJURATION_ALLOWLIST,slug
