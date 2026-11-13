"""One trusted engine boundary over the existing event journal and Memory projection."""
import hashlib
import json
from .engine_memory_contracts import pair, effects, effect_key, GameSnapshot
from .memory import encode

PROJECTED_KINDS = {'roll', 'damage', 'healing', 'resource', 'condition', 'movement',
                   'combat_started', 'combat_ended', 'effect'}
NATIVE_STATE_KINDS = {'attack_rolled': 'roll', 'save_rolled': 'roll', 'check_rolled': 'roll',
    'damage_applied': 'damage', 'healing_applied': 'healing', 'condition_applied': 'condition',
    'condition_removed': 'condition', 'actor_moved': 'movement', 'combatant_moved': 'movement',
    'effect_applied': 'effect', 'effect_expired': 'effect', 'combat_ended': 'combat_ended',
    'temphp_applied': 'temporary_hp'}
NATIVE_AUDIT_KINDS = {'round_started', 'round_ended', 'turn_started', 'turn_ended', 'turn_phase',
    'intent_submitted', 'zone_transit', 'dash_taken', 'move_failed', 'attack_failed', 'cast_failed',
    'area_targeted', 'reaction_triggered', 'spell_cast', 'combatant_joined', 'combatant_left',
    'concentration_check', 'concentration_dropped', 'unconscious', 'death_save_started',
    'death_save_rolled', 'stabilized', 'death', 'recharge_rolled', 'legendary_action_used',
    'legendary_resistance_used'}


class EngineMemoryAdapter:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def signature(event, snapshot):
        body = json.dumps([event.model_dump(mode='json'), snapshot.model_dump(mode='json')], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(body.encode('utf-8')).hexdigest()

    def deliver(self, event, snapshot):
        """Python-only entry point; intentionally not exposed to LLM tools or HTTP.

        Commits journal, supplied snapshot and Memory.put projection together. A retry
        returns the original event; a conflicting ID or abandoned branch is rejected.
        """
        event, snapshot = pair(event, snapshot)
        signature = self.signature(event, snapshot)
        with self.store.connection(True) as db:
            current = self.store.campaign(event.campaign_id, db)
            old = db.execute('''SELECT g.*,e.undone FROM memory_engine_events g
                JOIN events e ON e.id=g.event_id WHERE g.campaign=? AND g.source_event=?''',
                (event.campaign_id, event.id)).fetchone()
            if old:
                if old['signature'] != signature:
                    raise ValueError('Conflicting engine event ID')
                if old['undone']:
                    raise ValueError('Engine event belongs to an abandoned branch')
                return self.store.decode_event(db.execute('SELECT * FROM events WHERE id=?', (old['event_id'],)).fetchone())
            head = current['state'].get('engine_snapshot')
            if event.parent_event_id != (head['event_id'] if head else None) or event.sequence != (head['sequence'] + 1 if head else 1):
                raise ValueError('Engine parent/sequence does not match current head')
            used = db.execute('SELECT MAX(sequence) FROM memory_engine_events WHERE campaign=? AND branch_id=?',
                              (event.campaign_id, event.branch_id)).fetchone()[0]
            if used is not None and (not head or head['branch_id'] != event.branch_id or used >= event.sequence):
                raise ValueError('Fork requires a new branch ID')
            if head:
                before = GameSnapshot.model_validate(head)
                seen = set()
                for effect in effects(event):
                    kind, p = effect['type'], effect['payload']
                    key = effect_key(kind, p)
                    if 'target' not in p or key in seen:
                        continue
                    seen.add(key)
                    actor = next((a for a in before.actors if a.id == p['target']), None)
                    if actor is None:
                        continue  # First observed participant: no invented prehistory.
                    if kind == 'resource' and p.get('resource') not in actor.resources:
                        continue  # Newly initialized resource has no earlier observed value.
                    actual = {'damage': actor.hp.current, 'healing': actor.hp.current,
                              'resource': actor.resources[p['resource']].current if p.get('resource') in actor.resources else None,
                              'movement': list(actor.position.coordinates)}.get(kind)
                    if 'before' in p and actual != p['before']:
                        raise ValueError('Event before value does not match current snapshot')

            def transition(state):
                state['engine_snapshot'] = snapshot.model_dump(mode='json')
                return [dict(type='scripted', game_event=event.model_dump(mode='json'),
                             game_snapshot=snapshot.model_dump(mode='json'))]

            return self.store.engine_event(db, current, 'engine-memory:' + event.id,
                                           'Подтверждённое событие движка: ' + event.type,
                                           transition, signature=signature)

    def project(self, db, journal_id, raw_event, raw_snapshot):
        """Called by Memory.ingest, including journal backfill; never reads prose."""
        event, snapshot = pair(raw_event, raw_snapshot)
        source = db.execute('SELECT campaign,results,before_state FROM events WHERE id=?', (journal_id,)).fetchone()
        if source is None or source['campaign'] != event.campaign_id:
            raise ValueError('Missing or foreign journal event')
        memory = self.store.memory
        before_raw = json.loads(source['before_state']).get('engine_snapshot')
        before_snapshot = GameSnapshot.model_validate(before_raw) if before_raw else None
        actors = {a.id: memory.entity(db, event.campaign_id, a.kind, a.name, a.id,
                                    source_ref='GameSnapshot.actors:' + a.id) for a in snapshot.actors}
        closed = None
        if any(e['type'] == 'combat_ended' for e in effects(event)):
            native = next((r for r in json.loads(source['results']) if r.get('kind') == 'nat20_command'
                           and r.get('game_event', {}).get('id') == event.id), None)
            if native:
                # The post-close snapshot correctly has id="none". Keep the actual
                # closed encounter identity in memory, from Nat20's typed result.
                from dnd5e_engine.outcome import CombatOutcome
                CombatOutcome.model_validate(native['native_result']['outcome'])
                initial = native['native_result']['closed_combat']['initial']
                before = GameSnapshot.model_validate(json.loads(source['before_state'])['engine_snapshot'])
                if initial['session_id'] != before.combat.id or not before.combat.active or snapshot.combat.active:
                    raise ValueError('Native closed combat does not match journal snapshots')
                closed = dict(combat_id=initial['session_id'], participants=[a.id for a in before.actors])
                for actor in before.actors:
                    if actor.id not in actors:
                        actors[actor.id] = memory.entity(db, event.campaign_id, actor.kind, actor.name,
                            actor.id, source_ref='GameSnapshot.before.actors:' + actor.id)
        location = memory.entity(db, event.campaign_id, 'location', snapshot.location,
                                 source_ref='GameSnapshot.location')
        values = (journal_id, event.campaign_id, event.id, event.branch_id, event.sequence,
                  event.parent_event_id, self.signature(event, snapshot))
        prior = db.execute('SELECT * FROM memory_engine_events WHERE event_id=?', (journal_id,)).fetchone()
        if prior and tuple(prior) != values:
            raise ValueError('Conflicting persisted engine projection')
        if not prior:
            db.execute('''INSERT INTO memory_engine_events
                (event_id,campaign,source_event,branch_id,sequence,parent_event,signature) VALUES(?,?,?,?,?,?,?)''', values)
        # Content is a deterministic projection of typed fields, never an engine/GM summary.
        for index, effect in enumerate(effects(event)):
            kind, payload = effect['type'], effect['payload']
            if kind not in PROJECTED_KINDS:
                continue  # Retained in the journal; context reports unsupported projection.
            if kind == 'combat_ended' and closed is not None:
                payload = closed
            recipients = set(event.known_by)
            scope = event.scope
            target = next((a for a in snapshot.actors if a.id == payload.get('target')), None)
            access = [target] if target else []
            previous = next((a for a in before_snapshot.actors if target and a.id == target.id), None) if before_snapshot else None
            if target:
                if kind in ('damage', 'healing'):access.append(target.hp)
                if kind == 'resource':access.append(target.resources.get(payload['resource']))
                if kind == 'movement':access.append(target.position)
                if kind == 'condition':
                    source_actor = target if payload['active'] else previous
                    access.append(next((c for c in source_actor.conditions if c.id == payload['condition']), None) if source_actor else None)
                if kind == 'effect':
                    source_effects = snapshot.effects if payload['active'] else (before_snapshot.effects if before_snapshot else [])
                    access.append(next((e for e in source_effects or [] if (e.target,e.id,e.origin)==
                        (payload['target'],payload['effect_id'],payload['origin'])), None))
            if kind in ('combat_started','combat_ended'):
                access.append(before_snapshot.combat if kind=='combat_ended' and before_snapshot else snapshot.combat)
            for restriction in access:
                if restriction is None:continue
                recipients.intersection_update(restriction.known_by)
                if ('public','private','secret','gm').index(restriction.scope) > ('public','private','secret','gm').index(scope):
                    scope = restriction.scope
            subjects = set(event.subjects)
            if 'target' in payload:
                subjects.add(payload['target'])
            subjects.update(payload.get('participants', []))
            if 'participants' in payload:
                known_actors={a.id:a for a in (before_snapshot.actors if before_snapshot else [])}
                known_actors.update({a.id:a for a in snapshot.actors})
                payload={**payload,'participants':[identity for identity in payload['participants']
                    if identity in known_actors and known_actors[identity].scope not in ('secret','gm')
                    and recipients <= set(known_actors[identity].known_by)]}
            suffix = ':effect:' + str(index) if event.type == 'command_resolved' else ''
            source_ref = event.source_ref + ('.effects[' + str(index) + ']' if suffix else '')
            if kind == 'combat_ended' and closed is not None:
                source_ref += ';native_result.closed_combat.initial.session_id'
            memory.put(db, key='engine:' + event.id + suffix, campaign=event.campaign_id, event=journal_id,
                       kind=kind, provenance='confirmed_event', truth='fact', scope=scope,
                       location=location, text='Движок: ' + kind + '. ' + encode(payload), importance=7,
                       minutes=event.game_minutes, recipients=[actors[a] for a in recipients],
                       subjects=[actors[a] for a in subjects], source_ref=source_ref)

    @staticmethod
    def projection_issues(db, campaign, viewer, as_of):
        """Bounded diagnostics over retained journal data, never a new fact/journal.

        Unknown requiredness stays unknown. Missing known state projection is explicit;
        raw optional payloads stay in results rather than being guessed into world truth.
        """
        issues = []
        for row in db.execute('''SELECT id,seq,results FROM events WHERE campaign=? AND undone=0
                                AND seq<=? ORDER BY seq DESC LIMIT 48''', (campaign, as_of)):
            for number, result in enumerate(json.loads(row['results'])):
                event = result.get('game_event')
                if not event or event.get('scope') in ('secret', 'gm') or viewer not in event.get('known_by', []):
                    continue
                projected = event.get('payload', {}).get('effects', []) if event.get('type') == 'command_resolved' else [dict(type=event.get('type'), payload=event.get('payload', {}))]
                def issue(kind, ref, required=None):
                    issues.append(dict(event_id=row['id'], source_event=event['id'], as_of=row['seq'],
                        event_type=str(kind)[:80], status='unsupported_projection',
                        required=required if type(required) is bool else None, source_ref=ref,
                        raw_preserved=True))
                for index, effect in enumerate(projected):
                    if effect.get('type') not in PROJECTED_KINDS:
                        ref=f'events.results[{number}].game_event.payload'
                        if event.get('type')=='command_resolved':ref+=f'.effects[{index}]'
                        issue(effect.get('type', 'unknown'), ref,
                              effect.get('required'))
                for index, native in enumerate(result.get('native_events', [])):
                    if not isinstance(native,dict):
                        issue('malformed_native_event', f'events.results[{number}].native_events[{index}]')
                        continue
                    kind = native.get('type', 'unknown')
                    if not isinstance(kind,str):kind='malformed_native_event'
                    ref = f'events.results[{number}].native_events[{index}]'
                    expected = NATIVE_STATE_KINDS.get(kind)
                    if expected:
                        effect_body=native.get('effect') if isinstance(native.get('effect'),dict) else {}
                        target = native.get('target_id', effect_body.get('target_id'))
                        if not any(e.get('type') == expected and
                                   (not target or e.get('payload', {}).get('target', target) == target) for e in projected):
                            issue(kind, ref, True)
                    elif kind not in NATIVE_AUDIT_KINDS:
                        issue(kind, ref, native.get('required'))
                    if len(issues) >= 12:
                        return issues[:12]
        return issues[:12]

    def context(self, campaign, *, npc_id=None, query='', as_of=None, branch_id=None, limit=6, budget=6000, current_snapshot=None):
        """One audience per packet. as_of is a Solo journal seq, not engine sequence.

        Historical packets use the persisted historical snapshot. Existing active-event
        filtering also removes abandoned facts and knowledge after undo/restore.
        """
        with self.store.connection() as db:
            db.execute('BEGIN')  # Snapshot and ACL retrieval share one SQLite read snapshot.
            current = self.store.campaign(campaign, db)
            sql = 'SELECT seq,after_state FROM events WHERE campaign=? AND undone=0'
            args = [campaign]
            if as_of is not None:
                sql += ' AND seq<=?'
                args.append(as_of)
            row = db.execute(sql + ' ORDER BY seq DESC LIMIT 1', args).fetchone()
            recorded_state = json.loads(row['after_state']) if row else current['state']
            stored = recorded_state.get('engine_snapshot')
            raw = current_snapshot if current_snapshot is not None else stored
            if as_of is not None and stored and current_snapshot is not None:
                if any(current_snapshot.get(k) != stored.get(k) for k in ('event_id', 'branch_id', 'sequence')):
                    raw = stored  # Historical memory must not accompany future mechanics.
            if not raw:
                raise ValueError('No engine snapshot at requested point')
            snapshot = GameSnapshot.model_validate(raw)
            if snapshot.campaign_id != campaign:
                raise ValueError('Foreign context snapshot')
            journal_seq = row['seq'] if row else 0
            if branch_id is not None and snapshot.branch_id != branch_id:
                raise ValueError('Requested branch is not active at as_of')
            viewer = npc_id if npc_id is not None else 'hero'
            actor = next((a for a in snapshot.actors if a.id == viewer), None)
            # Social NPCs have canonical identities/ACL, not invented combat stats.
            npc_place = next((place for place in recorded_state.get('locations', [])
                if npc_id is not None and any(str(n.get('id', n['name'])) == npc_id and n.get('present',True)
                for n in place.get('npc', []))), None)
            social_npc = npc_place is not None
            if (actor is None and not social_npc) or (npc_id is not None and actor is not None and actor.kind == 'hero'):
                raise ValueError('Unknown NPC/audience')
            entity = db.execute('SELECT id FROM memory_entities WHERE campaign=? AND kind=? AND identity=?',
                                (campaign, actor.kind if actor else 'npc', viewer)).fetchone()
            if entity is None:
                raise ValueError('Unregistered context audience')
            entity = entity[0]
            location = db.execute("SELECT id FROM memory_entities WHERE campaign=? AND kind='location' AND identity=?",
                                  (campaign, npc_place['name'] if npc_place else recorded_state.get('location', snapshot.location))).fetchone()
            location = location[0] if location else ''

            def visible(value):
                return value.scope not in ('secret', 'gm') and viewer in value.known_by

            actors = []
            for actor in snapshot.actors:
                if not visible(actor):
                    continue
                item = dict(id=actor.id, kind=actor.kind, name=actor.name)
                if visible(actor.hp):
                    item['hp'] = actor.hp.model_dump(exclude={'scope', 'known_by'})
                if visible(actor.position):
                    item['position'] = list(actor.position.coordinates)
                item['resources'] = {key: value.current for key, value in actor.resources.items() if visible(value)}
                item['conditions'] = [value.id for value in actor.conditions if visible(value)]
                actors.append(item)
            combat = snapshot.combat.model_dump(exclude={'known_by', 'scope'}) if visible(snapshot.combat) else None
            if combat and combat['turn'] not in {a['id'] for a in actors}:
                combat['turn'] = None
            found = self.store.memory.retrieve(campaign, audience=[entity], query=query, subjects=[entity],
                                              location=location, as_of=journal_seq, limit=limit, budget=budget, _db=db)
            mechanics = dict(actors=actors, combat=combat)
            if snapshot.effects is not None:
                visible_ids = {a['id'] for a in actors}
                mechanics['effects'] = [e.model_dump(exclude={'scope', 'known_by'}) for e in snapshot.effects
                                        if visible(e) and {e.target, e.caster} <= visible_ids]
            packet = dict(campaign_id=campaign, branch_id=snapshot.branch_id, as_of=journal_seq,
                        source_event=snapshot.event_id, engine_sequence=snapshot.sequence,
                        audience=entity, mechanics=mechanics,
                        memories=found['records'],
                        npc_id=npc_id,
                        npc_knowledge=[r['id'] for r in found['records']] if npc_id is not None else [])
            place = npc_place or next((p for p in recorded_state.get('locations', []) if p['name'] == recorded_state.get('location')), None)
            if place:
                from .world import location_view
                scene = location_view(place,audience=viewer)
                packet['scene'] = dict(location=scene['name'], description=scene['description'],
                    objects=scene['objects'], facts=scene['facts'][-12:],
                    npc=[dict(id=n.get('id', n['name']), name=n['name']) for n in scene['npc']])
                if npc_place:
                    person=next(n for n in npc_place['npc'] if str(n.get('id',n['name']))==viewer and n.get('present',True))
                    known=person.get('known','')
                    if isinstance(known,str):
                        packet['npc_profile']=dict(id=viewer,name=person['name'],known=known[:1200],
                            source_ref='state.locations.npc:'+viewer+'.known',truth='belief')
            inventory = []
            for item in recorded_state.get('economy', {}).get('items', []):
                if item.get('quantity') and item.get('owner') in (viewer, 'npc:' + viewer):
                    inventory.append({k: item[k] for k in ('id', 'catalog', 'name', 'quantity', 'condition') if k in item})
            if inventory:
                packet['inventory'] = inventory[:40]
            issues = self.projection_issues(db, campaign, viewer, journal_seq)
            if issues:
                packet['projection_issues'] = issues
            return packet
