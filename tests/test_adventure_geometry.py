import pytest
from pydantic import ValidationError
from solo.geometry import rectangular_map
from solo.adventure_geometry import PreparedMap

def geometry():
    return dict(calibration={'image_width':1000,'cell_pixels':50},battle_map=rectangular_map(6,6).model_dump(),hero_position={'x':1,'y':1},enemy_position={'x':4,'y':4},source_checked=True)

def test_unchecked_image_is_reference_not_mechanical_geometry():
    body=geometry();body['source_checked']=False
    with pytest.raises(ValueError):PreparedMap.model_validate(body).checked()
    assert PreparedMap.model_validate(geometry()).checked().battle_map.width==6

def test_geometry_requires_walkable_distinct_entries():
    body=geometry();body['enemy_position']=body['hero_position']
    with pytest.raises(ValueError):PreparedMap.model_validate(body).checked()
    body=geometry();body['battle_map']['cells'][7]['blocked']=True
    with pytest.raises(ValueError):PreparedMap.model_validate(body).checked()

def test_generated_description_cannot_masquerade_as_source_map_geometry():
    with pytest.raises(ValidationError):PreparedMap.model_validate({'environment':'room','seed':1})
