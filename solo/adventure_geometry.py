"""A checked image calibration and explicit mechanical map, never a guessed template."""
from pydantic import Field
from .contracts import Strict
from .geometry import BattleMap,Position

class Calibration(Strict):
    image_width: int=Field(ge=160,le=4096)
    origin_x: float=Field(default=0,ge=0,le=4096)
    origin_y: float=Field(default=0,ge=0,le=4096)
    cell_pixels: float=Field(ge=2,le=1000)
    feet_per_cell: int=Field(default=5,ge=5,le=5)

class PreparedMap(Strict):
    calibration: Calibration
    battle_map: BattleMap
    hero_position: Position
    enemy_position: Position
    source_checked: bool=False
    def checked(self):
        if not self.source_checked:raise ValueError('Сверьте сетку, стены, этажи и точки входа с исходной картой')
        cells={c.key():c for c in self.battle_map.cells}
        for position in (self.hero_position,self.enemy_position):
            if position.key() not in cells or cells[position.key()].blocked:raise ValueError('Начальная позиция не является проходимой клеткой')
        if self.hero_position.key()==self.enemy_position.key():raise ValueError('Начальные позиции совпадают')
        return self
