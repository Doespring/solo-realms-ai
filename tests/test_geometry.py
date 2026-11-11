import pytest
from solo.geometry import Position,Cell,BattleMap,Geometry,rectangular_map
from solo.rulesets import BUILTINS

def geometry(map=None,diagonal='five_feet'):
    preset=BUILTINS['custom'].model_copy(update={'diagonal':diagonal})
    return Geometry(map or rectangular_map(6,6),preset)

def test_obstacle_path_budget_and_no_solid_corner_squeeze():
    field=rectangular_map(6,6)
    for cell in field.cells:
        if cell.key() in [(1,0,0),(0,1,0)]:cell.blocked=cell.opaque=True
    g=geometry(field)
    with pytest.raises(ValueError):g.path((0,0,0),(2,2,0))
    assert not g.line_of_sight((0,0,0),(2,2,0))
    with pytest.raises(ValueError):g.path((2,2,0),(5,5,0),budget=10)
    path,cost=g.path((2,2,0),(5,5,0),budget=15)
    assert path[0]==(2,2,0) and path[-1]==(5,5,0) and cost==15

def test_alternating_diagonals_do_not_reset_after_each_step():
    g=geometry(diagonal='alternating')
    assert g.distance((0,0,0),(2,2,0))==15
    assert g.path((0,0,0),(2,2,0))[1]==15
    assert g.path((0,0,0),(1,1,0),diagonals=1)[1]==10

def test_height_is_mechanical_and_stairs_are_registered():
    field=rectangular_map(6,6)
    field.cells += [Cell(x=3,y=2,z=1),Cell(x=3,y=2,z=2)]
    g=geometry(field)
    with pytest.raises(ValueError):g.path((2,2,0),(3,2,1))
    bottom=next(c for c in field.cells if c.key()==(2,2,0));bottom.terrain='stairs';bottom.links=[Position(x=3,y=2,z=1)]
    path,cost=geometry(field).path((2,2,0),(3,2,1))
    assert cost==5 and path[-1][2]==1
    middle=next(c for c in field.cells if c.key()==(3,2,1));middle.opaque_floor=True
    assert not geometry(field).line_of_sight((3,2,0),(3,2,2))
    middle.opaque_floor=False
    assert geometry(field).line_of_sight((3,2,0),(3,2,2))
    assert geometry(field).distance((3,2,0),(3,2,2))==10

def test_geometry_rejects_duplicate_cells_and_teleport_links():
    field=rectangular_map(6,6);raw=field.model_dump();raw['cells'].append(raw['cells'][0])
    with pytest.raises(ValueError):BattleMap.model_validate(raw)
    field.cells[0].links=[Position(x=5,y=5)]
    with pytest.raises(ValueError):BattleMap.model_validate(field.model_dump())
