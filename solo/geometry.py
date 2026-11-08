"""Shared tactical geometry. Coordinates are grid cells; every unit equals five feet."""
import heapq,itertools,math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Strict

class Position(Strict):
    x: int = Field(ge=0,le=79)
    y: int = Field(ge=0,le=79)
    z: int = Field(default=0,ge=-6,le=20)
    def key(self):return (self.x,self.y,self.z)

class Cell(Position):
    terrain: Literal['floor','grass','road','water','rock','wall','wood','stairs','ladder','bridge'] = 'floor'
    blocked: bool = False
    opaque: bool = False
    opaque_floor: bool = False
    move_cost: int = Field(default=1,ge=1,le=6)
    cover: Literal[0,2,5,100] = 0
    light: Literal['bright','dim','dark'] = 'bright'
    secret: bool = False
    links: list[Position] = Field(default_factory=list,max_length=8)

class MapObject(Strict):
    id: str = Field(min_length=1,max_length=80)
    name: str = Field(min_length=1,max_length=120)
    kind: Literal['door','light','decoration']
    position: Position
    secret: bool = False
    open: bool = False
    locked: bool = False
    enabled: bool = True
    bright_ft: int = Field(default=20,ge=0,le=120)
    dim_ft: int = Field(default=40,ge=0,le=240)
    discovery_dc: int = Field(default=15,ge=1,le=40)
    @model_validator(mode='after')
    def valid(self):
        if self.dim_ft<self.bright_ft:raise ValueError('Тусклый свет не может иметь меньший радиус')
        return self

class BattleMap(Strict):
    width: int = Field(ge=3,le=80)
    height: int = Field(ge=3,le=80)
    cells: list[Cell] = Field(min_length=1,max_length=38400)
    name: str = Field(default='Тактическая сцена',max_length=120)
    seed: int = Field(default=0,ge=0,le=2147483647)
    objects: list[MapObject] = Field(default_factory=list,max_length=200)
    @model_validator(mode='after')
    def valid(self):
        keys={c.key() for c in self.cells}
        if len(keys)!=len(self.cells):raise ValueError('Повтор клетки карты')
        if any(c.x>=self.width or c.y>=self.height for c in self.cells):raise ValueError('Клетка вне границ карты')
        if any(link.key() not in keys for c in self.cells for link in c.links):raise ValueError('Ссылка лестницы ведёт вне поверхности')
        if any(max(abs(link.key()[i]-c.key()[i]) for i in range(3))!=1 for c in self.cells for link in c.links):raise ValueError('Связи поверхностей должны быть соседними шагами')
        if len({o.id for o in self.objects})!=len(self.objects) or any(o.position.key() not in keys for o in self.objects):raise ValueError('Некорректный объект карты')
        for obj in self.objects:
            if obj.kind=='door':
                cell=next(c for c in self.cells if c.key()==obj.position.key())
                if cell.blocked==obj.open or cell.opaque==obj.open:raise ValueError('Состояние двери не согласовано с геометрией')
        return self

class Geometry:
    def __init__(self,map,preset):
        self.map=map;self.preset=preset;self.cells={c.key():c for c in map.cells};self.light_cache={}
    def distance(self,a,b):
        delta=sorted([abs(a[i]-b[i]) for i in range(3)],reverse=True)
        if self.preset.diagonal=='euclidean':return math.sqrt(sum(n*n for n in delta))*5
        if self.preset.diagonal=='alternating':return (delta[0]+delta[1]//2)*5
        return delta[0]*5
    def can_stand(self,position,occupied=()):
        cell=self.cells.get(tuple(position));return cell is not None and not cell.blocked and tuple(position) not in occupied
    def neighbors(self,position,occupied=()):
        x,y,z=position;cell=self.cells.get(position)
        if not cell:return
        for dx,dy in itertools.product((-1,0,1),repeat=2):
            if dx==dy==0:continue
            dest=(x+dx,y+dy,z)
            if not self.can_stand(dest,occupied):continue
            # Conservatively prevent squeezing diagonally through solid corners.
            if dx and dy and (not self.can_stand((x+dx,y,z)) or not self.can_stand((x,y+dy,z))):continue
            yield dest
        for link in cell.links:
            if self.can_stand(link.key(),occupied):yield link.key()
    def edge_cost(self,a,b,diagonals=0):
        cost=self.distance(a,b)*self.cells[b].move_cost
        if self.preset.diagonal=='alternating' and sum(a[i]!=b[i] for i in range(3))>1 and max(abs(a[i]-b[i]) for i in range(3))==1:
            cost=(10 if diagonals%2 else 5)*self.cells[b].move_cost
        if a[2]!=b[2] and self.cells[a].terrain=='ladder':cost*=2
        return cost
    def path(self,start,target,*,occupied=(),budget=10000,diagonals=0):
        start,target=tuple(start),tuple(target)
        if not self.can_stand(start) or not self.can_stand(target,occupied):raise ValueError('Целевая клетка непроходима или занята')
        first=(start,diagonals%2);frontier=[(0,first)];costs={first:0};came={};expanded=0
        while frontier:
            cost,node=heapq.heappop(frontier)
            if cost!=costs[node]:continue
            position,parity=node
            if position==target:
                path=[node]
                while path[-1]!=first:path.append(came[path[-1]])
                return [p[0] for p in reversed(path)],cost
            expanded+=1
            if expanded>len(self.cells)*4:break
            for dest in self.neighbors(position,occupied):
                diagonal=sum(position[i]!=dest[i] for i in range(3))>1
                next_cost=cost+self.edge_cost(position,dest,parity);next_node=(dest,(parity+int(diagonal))%2)
                if next_cost<=budget and next_cost<costs.get(next_node,math.inf):costs[next_node]=next_cost;came[next_node]=node;heapq.heappush(frontier,(next_cost,next_node))
        raise ValueError('Нет допустимого пути в пределах доступного движения')
    def reachable(self,start,*,occupied=(),budget=30,diagonals=0):
        first=(tuple(start),diagonals%2);frontier=[(0,first)];costs={first:0};result={}
        while frontier:
            cost,node=heapq.heappop(frontier)
            if cost!=costs[node]:continue
            position,parity=node;result[position]=min(cost,result.get(position,math.inf))
            for dest in self.neighbors(position,occupied):
                next_node=(dest,(parity+int(sum(position[i]!=dest[i] for i in range(3))>1))%2)
                next_cost=cost+self.edge_cost(position,dest,parity)
                if next_cost<=budget and next_cost<costs.get(next_node,math.inf):costs[next_node]=next_cost;heapq.heappush(frontier,(next_cost,next_node))
        return result
    @staticmethod
    def supercover(a,b):
        # Amanatides–Woo with simultaneous boundary crossings expanded conservatively.
        start=tuple(a);end=tuple(b);node=[math.floor(n) for n in start];finish=tuple(math.floor(n) for n in end)
        delta=[end[i]-start[i] for i in range(3)];step=[1 if d>0 else -1 if d<0 else 0 for d in delta]
        tdelta=[abs(1/d) if d else math.inf for d in delta]
        tmax=[((node[i]+1-start[i])/delta[i] if delta[i]>0 else (start[i]-node[i])/(-delta[i]) if delta[i]<0 else math.inf) for i in range(3)]
        yield tuple(node)
        for _ in range(300):
            if tuple(node)==finish:return
            earliest=min(tmax);axes=[i for i,n in enumerate(tmax) if abs(n-earliest)<1e-9]
            for size in range(1,len(axes)+1):
                for subset in itertools.combinations(axes,size):
                    touched=node.copy()
                    for i in subset:touched[i]+=step[i]
                    yield tuple(touched)
            for i in axes:node[i]+=step[i];tmax[i]+=tdelta[i]
    def line_of_sight(self,a,b):
        a,b=tuple(a),tuple(b)
        eye=tuple(a[i]+(.9 if i==2 else .5) for i in range(3));end=tuple(b[i]+(.9 if i==2 else .5) for i in range(3))
        for key in self.supercover(eye,end):
            if key in (a,b):continue
            cell=self.cells.get(key)
            if cell and cell.opaque:return False
        # Floors are planes, not solid five-foot voxels; prevent sight through ceilings.
        if a[2]!=b[2]:
            for z in range(math.ceil(min(eye[2],end[2])),math.floor(max(eye[2],end[2]))+1):
                t=(z-eye[2])/(end[2]-eye[2]);x=eye[0]+t*(end[0]-eye[0]);y=eye[1]+t*(end[1]-eye[1])
                for cx,cy in itertools.product({math.floor(x-1e-9),math.floor(x+1e-9)},{math.floor(y-1e-9),math.floor(y+1e-9)}):
                    cell=self.cells.get((cx,cy,z))
                    if cell and cell.opaque_floor:return False
        return True
    def cover(self,a,b):
        if not self.line_of_sight(a,b):return 100
        # Explicit authored obstacles grant cover; height alone grants no universal bonus.
        value=self.cells[tuple(b)].cover
        eye=tuple(a[i]+(.9 if i==2 else .5) for i in range(3));end=tuple(b[i]+(.9 if i==2 else .5) for i in range(3))
        for pos in self.supercover(eye,end):
            if pos in (tuple(a),tuple(b)):continue
            cell=self.cells.get(pos)
            if cell:value=max(value,cell.cover)
        return value
    def illumination(self,position):
        position=tuple(position)
        if position in self.light_cache:return self.light_cache[position]
        levels={'dark':0,'dim':1,'bright':2};brightness=levels[self.cells[tuple(position)].light]
        for obj in self.map.objects:
            if obj.kind!='light' or not obj.enabled:continue
            distance=self.distance(obj.position.key(),tuple(position))
            if distance>obj.dim_ft or not self.line_of_sight(obj.position.key(),position):continue
            brightness=max(brightness,2 if distance<=obj.bright_ft else 1)
        result=['dark','dim','bright'][brightness];self.light_cache[position]=result;return result

def rectangular_map(width=12,height=10):
    return BattleMap(width=width,height=height,cells=[Cell(x=x,y=y) for y in range(height) for x in range(width)])
