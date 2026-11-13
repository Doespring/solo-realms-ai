"""Deterministic mechanical generation. Decorative textures never define collision."""
import random
from typing import Literal
from pydantic import Field
from .contracts import Strict
from .geometry import BattleMap,Cell,MapObject,Position,Geometry

TEMPLATES={'room':'Комната','forest_road':'Лесная дорога','ruins':'Руины с секретным проходом','bridge':'Разрушенный мост','balcony':'Зал с балконом','cellar':'Дом с подвалом'}
class MapDescription(Strict):
    environment: Literal['room','forest_road','ruins','bridge','balcony','cellar']='room'
    seed: int = Field(default=1,ge=0,le=2147483647)
    width: int = Field(default=16,ge=12,le=40)
    height: int = Field(default=12,ge=10,le=30)
    lighting: Literal['bright','dim','dark']='bright'
    trees: int = Field(default=12,ge=0,le=80)
    rocks: int = Field(default=6,ge=0,le=40)

def generate(description):
    d=description;rng=random.Random(d.seed);w,h=d.width,d.height;mid=h//2
    cells={(x,y,0):Cell(x=x,y=y,light=d.lighting) for y in range(h) for x in range(w)}
    objects=[];hero=Position(x=1,y=mid);foe=Position(x=w-3,y=mid)
    def wall(x,y,z=0):
        cells[x,y,z]=Cell(x=x,y=y,z=z,terrain='wall',blocked=True,opaque=True,cover=100,light=d.lighting)
    def door(id,x,y,*,secret=False):
        wall(x,y);objects.append(MapObject(id=id,name='Тайный проход' if secret else 'Дверь',kind='door',position=Position(x=x,y=y),secret=secret))
    if d.environment=='forest_road':
        for c in cells.values():c.terrain='road' if abs(c.y-mid)<=1 else 'grass'
        possible=[p for p in cells if abs(p[1]-mid)>1 and 1<p[0]<w-2]
        rng.shuffle(possible)
        for p in possible[:d.trees]:cells[p].terrain='wood';cells[p].blocked=True;cells[p].opaque=True;cells[p].cover=100
        for p in possible[d.trees:d.trees+d.rocks]:cells[p].terrain='rock';cells[p].blocked=True;cells[p].cover=2
    elif d.environment in ('ruins','room','cellar'):
        for x in range(w):wall(x,0);wall(x,h-1)
        for y in range(1,h-1):wall(0,y);wall(w-1,y)
        if d.environment=='ruins':
            for y in range(1,h-1):wall(w//2,y)
            door('main-door',w//2,mid)
            door('secret-door',w//2,2,secret=True)
        if d.environment=='cellar':
            for c in cells.values():c.opaque_floor=True
            # Consecutive stair steps descend into an otherwise roofed basement.
            for x in range(2,7):
                z=2-x;cells[x,mid,z]=Cell(x=x,y=mid,z=z,terrain='stairs',light=d.lighting)
            for x in range(6,w-1):
                for y in range(2,h-2):
                    if (x,y,-4) not in cells:cells[x,y,-4]=Cell(x=x,y=y,z=-4,terrain='floor',light='dark',opaque_floor=True)
            foe=Position(x=w-3,y=mid,z=-4)
            objects.append(MapObject(id='cellar-lamp',name='Подвальный светильник',kind='light',position=Position(x=w-4,y=mid,z=-4),bright_ft=15,dim_ft=30))
    elif d.environment=='bridge':
        for c in cells.values():c.terrain='grass'
        for x in range(w//2-1,w//2+2):
            for y in range(h):cells[x,y,0].terrain='water';cells[x,y,0].move_cost=2
        # Raised walkway; a safe staircase at either bank, and water below.
        for x in range(3,w-2):cells[x,mid,2]=Cell(x=x,y=mid,z=2,terrain='bridge',light=d.lighting)
        for x,z in [(1,0),(2,1),(3,2),(w-3,2),(w-2,1),(w-1,0)]:
            cells[x,mid,z]=Cell(x=x,y=mid,z=z,terrain='stairs',light=d.lighting)
        foe=Position(x=w-3,y=mid,z=2)
        hero=Position(x=1,y=mid)
    elif d.environment=='balcony':
        for c in cells.values():c.opaque_floor=True
        for x in range(2,7):
            z=x-2;cells[x,mid,z]=Cell(x=x,y=mid,z=z,terrain='stairs',light=d.lighting)
        for x in range(6,w-1):
            for y in range(2,h-2):
                if (x,y,4) not in cells:cells[x,y,4]=Cell(x=x,y=y,z=4,terrain='wood',opaque_floor=True,light=d.lighting)
        foe=Position(x=w-3,y=mid,z=4)
    if d.environment in ('balcony','cellar','bridge'):
        # Only authored neighbouring staircase cells connect levels.
        stair=sorted([c for c in cells.values() if c.terrain=='stairs'],key=lambda c:(c.x,c.y,c.z))
        for a,b in zip(stair,stair[1:]):
            if max(abs(a.key()[i]-b.key()[i]) for i in range(3))==1:
                a.links.append(Position(x=b.x,y=b.y,z=b.z));b.links.append(Position(x=a.x,y=a.y,z=a.z))
    if d.lighting!='bright':
        objects.append(MapObject(id='entry-lamp',name='Светильник у входа',kind='light',position=hero.model_copy(),bright_ft=15,dim_ft=30))
    field=BattleMap(width=w,height=h,cells=list(cells.values()),objects=objects,name=TEMPLATES[d.environment],seed=d.seed)
    validate(field,hero,foe)
    return field,hero,foe

def validate(field,hero,foe):
    from .rulesets import BUILTINS
    geometry=Geometry(field,BUILTINS['dnd2024'])
    if not geometry.can_stand(hero.key()) or not geometry.can_stand(foe.key()) or hero==foe:raise ValueError('Начальные позиции карты недопустимы')
    # Validate connectivity with unlocked doors conceptually open, never move actors through closed doors.
    opened=field.model_copy(deep=True)
    for o in opened.objects:
        if o.kind=='door' and not o.locked:
            c=next(c for c in opened.cells if c.key()==o.position.key());c.blocked=c.opaque=False;c.cover=0;o.open=True
    Geometry(opened,BUILTINS['dnd2024']).path(hero.key(),foe.key())
