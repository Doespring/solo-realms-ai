import pytest
from pydantic import ValidationError
from solo.adventure_contracts import AdventureDefinition, ScenarioEvent, Predicate, RollTable

SOURCE={'document':'synthetic','page':1,'review':'confirmed'}

def test_recommendation_is_not_unconditional_mandatory_event():
    with pytest.raises(ValidationError):
        ScenarioEvent(id='trap',category='mandatory',source=SOURCE)
    value=ScenarioEvent(id='pace',category='optional',instruction='recommendation',source=SOURCE)
    assert value.instruction=='recommendation'

def test_tables_reject_gaps_and_overlapping_dice_ranges():
    for rows in [[{'low':1,'high':5,'label':'a'}],
                 [{'low':1,'high':4,'label':'a'},{'low':4,'high':6,'label':'b'}]]:
        with pytest.raises(ValidationError):RollTable(id='encounters',formula='1d6',rows=rows,source=SOURCE)
    table=RollTable(id='encounters',formula='1d6',rows=[{'low':1,'high':6,'label':'a'}],source=SOURCE)
    assert table.rows[0].high==6

def test_conditions_are_data_not_paths_into_python_or_private_files():
    for path in ['__class__','hero.hp.__class__','documents.original','adventure.flags.key;exec']:
        with pytest.raises(ValidationError):Predicate(path=path,value=True)
    assert Predicate(path='adventure.trackers.alarm',op='gte',value=3).value==3

def test_dependency_graph_rejects_cycles_and_missing_events():
    for dependencies in [('b','a'),('missing','')]:
        events=[dict(id='a',category='optional',source=SOURCE,prerequisites=[dependencies[0]]),
                dict(id='b',category='optional',source=SOURCE,prerequisites=[dependencies[1]] if dependencies[1] else [])]
        with pytest.raises(ValidationError):AdventureDefinition(id='test',title='Test',ruleset='dnd2014',ruleset_version=1,events=events)
