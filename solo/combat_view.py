"""Basic server visibility for combat. Never send the canonical battle to the browser."""
from .combat import CombatEngine,WEAPONS

def key(position):return ','.join(str(n) for n in position)

def refresh_vision(engine):
    hero=next(a for a in engine.actors.values() if a.team=='hero')
    visible={hero.position.key()}
    for cell in engine.map.cells:
        if not cell.secret and engine.sees(hero,cell.key()):visible.add(cell.key())
    explored={tuple(p) for p in engine.data.get('explored',[])}|visible
    snapshots=engine.data.setdefault('known_cells',{})
    for pos in visible:
        cell=engine.geometry.cells.get(pos)
        if not cell or cell.secret:continue
        public=cell.model_dump();public.pop('secret',None);public['light']=engine.geometry.illumination(pos)
        # A link may identify an undiscovered staircase/room. Keep such links private.
        public['links']=[p for p in public['links'] if (p['x'],p['y'],p['z']) in visible or (cell.terrain in ('stairs','ladder') and not engine.geometry.cells[(p['x'],p['y'],p['z'])].secret)]
        snapshots[key(pos)]=public
    known=engine.data.setdefault('last_seen',{})
    for actor in engine.actors.values():
        if actor.id!=hero.id and engine.sees(hero,actor):
            known[actor.id]=dict(id=actor.id,name=actor.stats.name,position=actor.position.model_dump(),round=engine.data['round'])
    engine.data['explored']=[list(p) for p in sorted(explored)]
    return visible

def project(state):
    if not state.get('combat'):return None
    engine=CombatEngine(state);hero=next(a for a in engine.actors.values() if a.team=='hero')
    visible={hero.position.key()}|{c.key() for c in engine.map.cells if not c.secret and engine.sees(hero,c.key())}
    known=engine.data.get('last_seen',{})
    actors=[]
    for actor in engine.actors.values():
        if actor.id!=hero.id and not engine.sees(hero,actor):continue
        public=dict(id=actor.id,name=actor.stats.name,team=actor.team,position=actor.position.model_dump(),hp=actor.stats.hp,max_hp=engine.rules.exhaustion_effects(actor.stats)['effective_max_hp'],conditions=actor.stats.conditions,dead=actor.dead,initiative=actor.initiative)
        if actor.id==hero.id:
            public.update(action=actor.action,bonus_action=actor.bonus_action,reaction=actor.reaction,interaction=actor.interaction,weapon=actor.weapon,ammo=actor.ammo,
                movement_left_ft=max(0,engine.speed(actor)+actor.dash_movement-actor.movement_spent),speed_ft=engine.speed(actor))
            weapon=WEAPONS[actor.weapon]
            public.update(attack_range_ft=weapon.range_ft if weapon.ranged else weapon.reach_ft,long_range_ft=weapon.long_range_ft if weapon.ranged else weapon.reach_ft,registered_bonus=engine.rules.preset.id=='dnd2024' and actor.stats.class_name.lower() in ('rogue','плут') and actor.stats.level>=2 and 'cunning_action' in actor.stats.features)
        else:public['distance_from_hero_ft']=engine.geometry.distance(hero.position.key(),actor.position.key())
        actors.append(public)
    visible_ids={a['id'] for a in actors}
    # Explore snapshots retain what was actually seen, without live hidden changes.
    cells=[]
    for snap in engine.data.get('known_cells',{}).values():
        cells.append({**snap,'visible':(snap['x'],snap['y'],snap['z']) in visible})
    pending=engine.data.get('pending_reaction')
    current=engine.current()
    return dict(active=engine.data['active'],ended=engine.data.get('ended'),round=engine.data['round'],
        current_actor=current.id if current.id in visible_ids else None,current_team=current.team,
        order=[id for id in engine.data['order'] if id in visible_ids],actors=actors,
        last_seen=[{**v,'currently_visible':False} for id,v in known.items() if id not in visible_ids],
        map=dict(name=engine.map.name,width=engine.map.width,height=engine.map.height,cells=cells,objects=[o.model_dump(exclude={'secret','discovery_dc'}) for o in engine.map.objects if not o.secret and o.position.key() in visible]),
        pending_reaction=dict(target=pending['target'],choice=['attack','decline']) if pending else None,
        pending_death=engine.data.get('pending_death'),lethal_risk_declared=engine.data['lethal_risk_declared'])

def public_campaign(campaign):
    state=campaign.get('state',{})
    # Shallow copies replace only the protected battle subtree; canonical data is untouched.
    public={**state}
    public.pop('engine_snapshot',None)
    public.pop('nat20',None)  # Durable provider recipe/seed is trusted server state.
    if state.get('adventure'):
        from .adventure_engine import projection
        public['adventure']=projection(state)
    if state.get('combat'):public['combat']=project(state)
    if state.get('economy'):
        from .inventory import public_economy
        public['economy']=public_economy(state)
    if state.get('locations'):
        from .world import project as world_project,locations_view
        world=world_project(state);public['world']=world;public['locations']=locations_view(state)
        public['geography']={'nodes':world['nodes'],'routes':world['routes']}
    return {**campaign,'backend':'nat20' if 'nat20' in state else 'solo','state':public}
