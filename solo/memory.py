"""Event-derived memory, scoped knowledge and bounded retrieval. Never reads prose as truth."""
import hashlib
import json
import re
import time
import uuid
from contextlib import nullcontext

SCHEMA = '''
CREATE TABLE IF NOT EXISTS memory_entities(
 id TEXT PRIMARY KEY, campaign TEXT NOT NULL REFERENCES campaigns(id), kind TEXT NOT NULL,
 identity TEXT NOT NULL, name TEXT NOT NULL, aliases TEXT NOT NULL, source_ref TEXT NOT NULL,
 UNIQUE(campaign,kind,identity));
CREATE TABLE IF NOT EXISTS memory_records(
 id INTEGER PRIMARY KEY, record_key TEXT UNIQUE NOT NULL, campaign TEXT NOT NULL REFERENCES campaigns(id),
 event_id TEXT REFERENCES events(id) ON DELETE CASCADE, kind TEXT NOT NULL, provenance TEXT NOT NULL,
 truth TEXT NOT NULL, scope TEXT NOT NULL, location_id TEXT NOT NULL, content TEXT NOT NULL,
 importance INTEGER NOT NULL, game_minutes INTEGER NOT NULL, source_ref TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memory_access(
 record_id INTEGER NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
 entity_id TEXT NOT NULL REFERENCES memory_entities(id), PRIMARY KEY(record_id,entity_id));
CREATE TABLE IF NOT EXISTS memory_subjects(
 record_id INTEGER NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
 entity_id TEXT NOT NULL REFERENCES memory_entities(id), PRIMARY KEY(record_id,entity_id));
CREATE INDEX IF NOT EXISTS memory_campaign_kind ON memory_records(campaign,kind,importance DESC);
CREATE INDEX IF NOT EXISTS memory_source_event ON memory_records(event_id);
CREATE INDEX IF NOT EXISTS memory_known_by ON memory_access(entity_id,record_id);
CREATE INDEX IF NOT EXISTS memory_subject ON memory_subjects(entity_id,record_id);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(content,tokenize='unicode61');
CREATE TABLE IF NOT EXISTS memory_engine_events(
 event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
 campaign TEXT NOT NULL REFERENCES campaigns(id), source_event TEXT NOT NULL,
 branch_id TEXT NOT NULL, sequence INTEGER NOT NULL, parent_event TEXT,
 signature TEXT NOT NULL, UNIQUE(campaign,source_event), UNIQUE(campaign,branch_id,sequence));
'''

def encode(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))

STOP={'я','мы','ты','вы','он','она','оно','они','как','что','где','когда','это','тот','эта','мне','его','ее','не','на','у','и','в','с','о','об','из','к','по','за','до','для','а','но','если','только','пока','сейчас','помнишь','помню','помнить'}
SYNONYMS=[('обещ','обяз','клятв'),('подземел','катакомб','подвал'),('письм','документ','послан'),('помощ','помог','помогл','спас'),('брат','брату','брата')]

def words(text):
    result=[]
    for token in re.findall(r'[а-яёa-z0-9]+',str(text).lower().replace('ё','е')):
        if token in STOP or len(token)<3:continue
        # Lightweight Russian suffix normalization; identities/ACL do not depend on this heuristic.
        stem=re.sub(r'(иями|ями|ами|ого|ему|ому|ах|ях|ой|ей|ые|ий|ая|яя|ое|ее|ов|ев|ам|ям|ом|ем|у|ю|а|я|ы|и|е)$','',token)
        if len(stem)<3:stem=token
        result.append(stem)
        for group in SYNONYMS:
            if any(stem.startswith(x) for x in group):result.extend(group)
    return list(dict.fromkeys(result))[:32]

class Memory:
    def __init__(self,store):self.store=store

    @staticmethod
    def entity(db,campaign,kind,name,identity=None,aliases=(),source_ref=''):
        identity=str(identity or name)
        entity=str(uuid.uuid5(uuid.NAMESPACE_URL,'solo:'+campaign+':'+kind+':'+identity))
        names=list(dict.fromkeys([name,*aliases]))
        old=db.execute('SELECT aliases FROM memory_entities WHERE id=?',(entity,)).fetchone()
        if old:names=list(dict.fromkeys(json.loads(old[0])+names))
        db.execute('''INSERT INTO memory_entities VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,aliases=excluded.aliases,source_ref=excluded.source_ref''',
                   (entity,campaign,kind,identity,name,encode(names),source_ref))
        return entity

    def register(self,db,campaign,state):
        hero=self.entity(db,campaign,'hero',state.get('hero',{}).get('name','Герой'),identity='hero',source_ref='state.hero')
        locations={};npcs={}
        for p in state.get('locations',[]):
            # Names already identify locations in the existing kernel; no competing world copy.
            lid=self.entity(db,campaign,'location',p['name'],source_ref='state.locations:'+p['name'])
            locations[p['name']]=lid
            for obj in p.get('objects',[]):
                name=obj.get('name','') if isinstance(obj,dict) else str(obj)
                if name:self.entity(db,campaign,'object',name,source_ref='state.locations:'+p['name']+'.objects')
            for n in p.get('npc',[]):
                eid=self.entity(db,campaign,'npc',n['name'],n.get('id'),n.get('aliases',[]),'state.locations:'+p['name']+'.npc:'+n['name'])
                npcs[n['name']]=eid
        return hero,locations,npcs

    @staticmethod
    def put(db,*,key,campaign,event,kind,provenance,truth,scope,location,text,importance,minutes,recipients,subjects=(),source_ref=''):
        key=campaign+':'+key
        if truth not in ('fact','statement','rumour','belief','intent','attempt'):raise ValueError('Unknown memory truth class')
        if scope not in ('public','private','secret','gm'):raise ValueError('Unknown memory scope')
        if provenance not in ('scenario','rules','npc_statement','player_statement','author','hero_knowledge','confirmed_event'):raise ValueError('Untrusted memory provenance')
        if provenance in ('npc_statement','player_statement') and truth=='fact':raise ValueError('A statement cannot become world truth automatically')
        ids=set(recipients)|set(subjects)
        if ids:
            actual={r[0] for r in db.execute('SELECT id FROM memory_entities WHERE campaign=? AND id IN ('+','.join('?'*len(ids))+')',(campaign,*ids))}
            if actual!=ids:raise ValueError('Memory entities must belong to the same campaign')
        if event:
            row=db.execute('SELECT campaign,undone FROM events WHERE id=?',(event,)).fetchone()
            if not row or row[0]!=campaign:raise ValueError('Missing or foreign source event')
        cursor=db.execute('''INSERT OR IGNORE INTO memory_records(record_key,campaign,event_id,kind,provenance,truth,scope,location_id,content,importance,game_minutes,source_ref,created)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(key,campaign,event,kind,provenance,truth,scope,location,str(text)[:1200],max(0,min(10,importance)),minutes,source_ref,time.time()))
        rid=db.execute('SELECT id FROM memory_records WHERE record_key=?',(key,)).fetchone()[0]
        if cursor.rowcount:
            db.execute('INSERT INTO memory_fts(rowid,content) VALUES(?,?)',(rid,' '.join(words(text))))
            db.executemany('INSERT INTO memory_access VALUES(?,?)',[(rid,eid) for eid in set(recipients)])
            db.executemany('INSERT INTO memory_subjects VALUES(?,?)',[(rid,eid) for eid in set(subjects)])
        return rid

    def ingest(self,db,event_id):
        row=db.execute('''SELECT e.*,a.plan,a.original FROM events e JOIN actions a ON a.id=e.action WHERE e.id=?''',(event_id,)).fetchone()
        if not row:raise ValueError('Missing source event')
        campaign=row['campaign'];before=json.loads(row['before_state']);after=json.loads(row['after_state'])
        hero,locations,npcs=self.register(db,campaign,before)
        self.register(db,campaign,after)
        # Refresh IDs for arriving NPC not present in the before snapshot.
        _,after_locations,after_npcs=self.register(db,campaign,after)
        locations.update(after_locations);npcs.update(after_npcs)
        place_name=before['location'];plan=json.loads(row['plan']);results=json.loads(row['results'])
        minutes=int(after.get('elapsed_minutes',0))
        for result in results:
            if 'game_event' in result or 'game_snapshot' in result:
                from .engine_memory import EngineMemoryAdapter
                EngineMemoryAdapter(self.store).project(db,event_id,result.get('game_event'),result.get('game_snapshot'))
        self.project_inventory(db, campaign, event_id, before, after, hero, locations, minutes)
        def witnesses(name,state):
            place=next((p for p in state.get('locations',[]) if p['name']==name),{})
            return [npcs[n['name']] for n in place.get('npc',[]) if n.get('present',True) and n['name'] in npcs]
        for index,result in enumerate(results):
            step=plan.get('steps',[])[index] if index<len(plan.get('steps',[])) else {}
            kind=result.get('type','unknown');action=step.get('text',row['original'])
            if kind=='scripted':continue
            recipients=[hero,*witnesses(place_name,before)];subjects=[hero]
            if step.get('target') in npcs:subjects.append(npcs[step['target']])
            scope='public';truth='fact';importance=3;memory_kind='event'
            if kind in ('move','travel_stop'):
                place_name=result.get('target',after['location'])
                recipients=[hero,*witnesses(place_name,after)]
                text='Герой прибыл: '+place_name+'. '+result.get('text','')
                memory_kind='travel';importance=4
            elif kind=='speak':
                text='Герой сказал: '+action;truth='statement';memory_kind='conversation';importance=4
                if re.search(r'\b(?:шепчу|шепотом|наедине)\b',action.lower().replace('ё','е')):
                    recipients=[hero]+([npcs[step['target']]] if step.get('target') in npcs and npcs[step['target']] in recipients else [])
                    scope='private'
                if re.search(r'(?<!не )\b(?:обещаю|обязуюсь|клянусь)\b',action,re.I):memory_kind='promise';importance=9
            elif kind=='check':
                text='Попытка: '+action+'. Подтверждённый результат: '+('успех' if result.get('success') else 'неудача')
                memory_kind='check';importance=6
            elif kind=='canon':
                text='Подтверждено игроком: '+result['text'];importance=8;recipients=[hero];scope='private'
            elif kind in ('deferred','denied','halted') or step.get('disposition')!='now':
                text='Не исполнено: '+action+('. Условие: '+step['condition'] if step.get('condition') else '')
                truth='intent';scope='private';recipients=[hero];memory_kind='intention';importance=4
            elif kind in ('unsupported','needs_context','impossible'):
                text='Неподдержанная попытка: '+action+'. Результат не установлен.';truth='attempt';importance=2
            else:
                text='Подтверждённое событие: '+action+'. '+result.get('reply',result.get('text',''))
            self.put(db,key=event_id+':step:'+str(index),campaign=campaign,event=event_id,kind=memory_kind,
                     provenance='player_statement' if truth=='statement' else 'confirmed_event',truth=truth,scope=scope,
                     location=locations.get(place_name,''),text=text,importance=importance,minutes=minutes,
                     recipients=recipients,subjects=[*subjects,*recipients,*([locations[place_name]] if place_name in locations else [])],source_ref='events.results['+str(index)+']')
        # Structured engine/scenario evidence only. No extraction from Narrator text.
        for index,result in enumerate(results):
            for number,evidence in enumerate(result.get('memory_evidence',[])):
                if evidence.get('provenance') not in ('scenario','rules','npc_statement','author','hero_knowledge'):
                    raise ValueError('Untrusted memory evidence source')
                recipients=[self.resolve_actor(db,campaign,n) for n in evidence.get('known_by',[])]
                if not recipients:continue
                self.put(db,key=event_id+':evidence:'+str(index)+':'+str(number),campaign=campaign,event=event_id,
                         kind=evidence.get('kind','event'),provenance=evidence['provenance'],truth=evidence.get('truth','statement'),
                         scope=evidence.get('scope','private'),location=locations.get(place_name,''),text=evidence['text'],
                         importance=evidence.get('importance',7),minutes=minutes,recipients=recipients,
                         subjects=[self.resolve_actor(db,campaign,n) for n in evidence['subjects']] if 'subjects' in evidence else recipients,
                         source_ref=evidence.get('source_ref','events.results.memory_evidence'))
        # Changes in NPC canonical data are referenced, not inferred from a polite line.
        old={n.get('id',n['name']):n for p in before.get('locations',[]) for n in p.get('npc',[])}
        for p in after.get('locations',[]):
            for n in p.get('npc',[]):
                prior=old.get(n.get('id',n['name']),{})
                for field in ('relationships','goals','conflicts','beliefs','known'):
                    if n.get(field)!=prior.get(field) and field in n:
                        eid=npcs[n['name']]
                        self.put(db,key=event_id+':npc:'+eid+':'+field,campaign=campaign,event=event_id,
                                 kind='relationship' if field=='relationships' else 'npc_state',provenance='confirmed_event',
                                 truth='belief' if field=='beliefs' else 'fact',scope='private',location=locations[p['name']],
                                 text=n['name']+': изменено '+field+'. '+encode(n[field]),importance=8,minutes=minutes,
                                 recipients=[eid],subjects=[eid],source_ref='state.npc.'+field)

    @staticmethod
    def resolve_actor(db,campaign,identity):
        """Stable ID first; legacy names only when unambiguous within the campaign."""
        rows=db.execute("SELECT id,identity,name,aliases FROM memory_entities WHERE campaign=? AND kind NOT IN ('location','object')",(campaign,)).fetchall()
        for field in ('id','identity'):
            exact=[r['id'] for r in rows if r[field]==identity]
            if len(exact)==1:return exact[0]
            if len(exact)>1:raise ValueError('Ambiguous knowledge recipient; use stable ID')
        matches={r['id'] for r in rows if identity==r['name'] or identity in json.loads(r['aliases'])}
        if len(matches)!=1:raise ValueError('Unknown or ambiguous knowledge recipient; use stable ID')
        return matches.pop()

    def project_inventory(self,db,campaign,event,before,after,hero,locations,minutes):
        """Projection only of committed canonical inventory, never prose or item rules.

        Each owner sees their own before/after; a concealed previous owner is not
        disclosed to the recipient. Explicit witnessed transfers can add evidence ACL.
        """
        if not before.get('economy') and not after.get('economy'):return
        from .rulesets import digest
        aliases={'hero':hero}
        for state in (before,after):
            for place in state.get('locations',[]):
                for npc in place.get('npc',[]):
                    entity=self.resolve_actor(db,campaign,str(npc.get('id',npc['name'])))
                    aliases['npc:'+str(npc.get('id',npc['name']))]=entity
                    aliases['npc:'+digest([place['name'],npc['name']])[:24]]=entity
                    aliases['npc:'+entity]=entity
        old={i['id']:i for i in before.get('economy',{}).get('items',[])}
        new={i['id']:i for i in after.get('economy',{}).get('items',[])}
        fields=('id','catalog','name','quantity','owner','condition')
        location=locations.get(after.get('location'),'')
        def item_view(item):return {k:item[k] for k in fields if k in item} if item else None
        for identity in sorted(old.keys()|new.keys()):
            left,right=old.get(identity),new.get(identity)
            if item_view(left)==item_view(right):continue
            item=right or left
            subject=self.entity(db,campaign,'object',item['name'],'item:'+identity,source_ref='state.economy.items:'+identity)
            owners={aliases.get(i.get('owner')) for i in (left,right) if i};owners.discard(None)
            for owner in owners:
                own_before=left if left and aliases.get(left.get('owner'))==owner else None
                own_after=right if right and aliases.get(right.get('owner'))==owner else None
                self.put(db,key=event+':item:'+identity+':'+owner,campaign=campaign,event=event,
                    kind='inventory',provenance='confirmed_event',truth='fact',scope='private',location=location,
                    text='Предмет: '+encode(dict(before=item_view(own_before),after=item_view(own_after))),
                    importance=6,minutes=minutes,recipients=[owner],subjects=[owner,subject],
                    source_ref='state.economy.items:'+identity)
        if before.get('economy',{}).get('wallet_cp',0)!=after.get('economy',{}).get('wallet_cp',0):
            self.put(db,key=event+':wallet',campaign=campaign,event=event,kind='resource',
                provenance='confirmed_event',truth='fact',scope='private',location=location,
                text='Кошелёк: '+encode(dict(before=before.get('economy',{}).get('wallet_cp',0),after=after.get('economy',{}).get('wallet_cp',0))),
                importance=6,minutes=minutes,recipients=[hero],subjects=[hero],source_ref='state.economy.wallet_cp')

    def migrate(self,db):
        for row in db.execute('SELECT id,state FROM campaigns').fetchall():self.register(db,row['id'],json.loads(row['state']))
        for row in db.execute('SELECT id FROM events ORDER BY seq').fetchall():self.ingest(db,row['id'])

    def identities(self,campaign,state):
        # Reading a scene never mutates canonical Game State.
        with self.store.connection() as db:
            rows=db.execute('SELECT * FROM memory_entities WHERE campaign=?',(campaign,)).fetchall()
        by_key={(r['kind'],r['identity']):r['id'] for r in rows}
        return dict(hero=by_key.get(('hero','hero')),
                    npc={n['name']:by_key.get(('npc',str(n.get('id',n['name'])))) for p in state.get('locations',[]) for n in p.get('npc',[])},
                    location=by_key.get(('location',state['location'])))

    def retrieve(self,campaign,*,audience,query='',location='',subjects=(),as_of=None,limit=5,budget=1400,_db=None):
        started=time.perf_counter();audience=list(dict.fromkeys(x for x in audience if x))
        if not audience or limit<=0 or budget<=0:return {'records':[],'search_ms':0,'candidates':0}
        terms=words(query);candidates={}
        with (self.store.connection() if _db is None else nullcontext(_db)) as db:
            # Entity names support entity matching separately; do not drown FTS with common NPC names.
            entities=[dict(r) for r in db.execute('SELECT * FROM memory_entities WHERE campaign=?',(campaign,))]
            mentioned=set(subjects)
            query_words=set(terms)
            for entity in entities:
                alias_words=set(w for alias in json.loads(entity['aliases']) for w in words(alias))
                if query_words & alias_words:mentioned.add(entity['id']);query_words-=alias_words
            access=' AND '.join('EXISTS(SELECT 1 FROM memory_access a WHERE a.record_id=m.id AND a.entity_id=?)' for _ in audience)
            base='''SELECT m.*,e.seq,g.source_event,g.branch_id,g.sequence AS engine_sequence
                    FROM memory_records m JOIN events e ON e.id=m.event_id
                    LEFT JOIN memory_engine_events g ON g.event_id=e.id
                    WHERE m.campaign=? AND e.undone=0 AND m.scope NOT IN ('secret','gm') AND '''+access
            params=[campaign,*audience]
            if as_of is not None:base+=' AND e.seq<=?';params.append(as_of)
            def collect(sql,args):
                for row in db.execute(sql,args):candidates[row['id']]=dict(row)
            if query_words:
                expression=' OR '.join('"'+word+'"*' for word in sorted(query_words)[:20])
                collect(base+' AND m.id IN (SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?) ORDER BY m.importance DESC,e.seq DESC LIMIT 80',[*params,expression])
            if mentioned:
                collect(base+' AND EXISTS(SELECT 1 FROM memory_subjects s WHERE s.record_id=m.id AND s.entity_id IN ('+','.join('?'*len(mentioned))+')) ORDER BY m.importance DESC,e.seq DESC LIMIT 64',[*params,*mentioned])
            collect(base+' ORDER BY m.importance DESC,e.seq DESC LIMIT 48',params)
            record_subjects={r['id']:{s[0] for s in db.execute('SELECT entity_id FROM memory_subjects WHERE record_id=?',(r['id'],))} for r in candidates.values()}
            hidden_subjects={}
            source_ids={r['event_id'] for r in candidates.values() if r['source_event'] is not None}
            identities={(e['kind'],e['identity']):e['id'] for e in entities}
            viewers={e['identity'] for e in entities if e['id'] in audience}
            if source_ids:
                for source in db.execute('''SELECT e.id,json_extract(e.before_state,'$.engine_snapshot') AS previous,
                        json_extract(result.value,'$.game_snapshot') AS snapshot
                        FROM events e, json_each(e.results) result WHERE e.id IN ('''+','.join('?'*len(source_ids))+')',tuple(source_ids)):
                    hidden=hidden_subjects.setdefault(source['id'],set())
                    for raw in (source['previous'],source['snapshot']):
                        for actor in (json.loads(raw) if raw else {}).get('actors',[]):
                            if actor.get('scope') in ('secret','gm') or not viewers <= set(actor.get('known_by',[])):
                                hidden.add(identities.get((actor['kind'],actor['id'])))
        latest=max((r['seq'] for r in candidates.values()),default=0)
        def rank(r):
            tokens=set(words(r['content']))
            lexical=sum(any(token.startswith(t) for token in tokens) for t in query_words)
            return lexical*12+len(mentioned & record_subjects[r['id']])*4+(3 if r['location_id']==location else 0)+r['importance']+max(0,3-(latest-r['seq'])/20)
        selected=[];used=0
        for row in sorted(candidates.values(),key=lambda r:(rank(r),r['seq']),reverse=True):
            item={k:row[k] for k in ('id','event_id','kind','truth','provenance','content','game_minutes','source_ref')}
            item['content']=row['content'][:480]
            if row['source_event'] is not None:
                item.update(source_event=row['source_event'],branch_id=row['branch_id'],
                            as_of=row['seq'],engine_sequence=row['engine_sequence'],scope=row['scope'],
                            subjects=sorted(record_subjects[row['id']]-hidden_subjects.get(row['event_id'],set())))
            size=len(encode(item))
            if used+size>budget:continue
            selected.append(item);used+=size
            if len(selected)>=max(0,min(10,limit)):break
        return dict(records=selected,search_ms=round((time.perf_counter()-started)*1000,3),candidates=len(candidates),chars=used)

    def audit(self):
        with self.store.connection() as db:
            missing=[dict(r) for r in db.execute('SELECT m.id,m.event_id FROM memory_records m LEFT JOIN events e ON e.id=m.event_id WHERE m.event_id IS NOT NULL AND e.id IS NULL')]
            return dict(dangling_events=missing,foreign_key_errors=[tuple(r) for r in db.execute('PRAGMA foreign_key_check')])

    def knows(self,campaign,entity,record,as_of=None):
        """Program-only knowledge check, including secrets; SceneCompiler never serializes secrets."""
        with self.store.connection() as db:
            row=db.execute('''SELECT e.seq FROM memory_records m JOIN memory_access a ON a.record_id=m.id
                JOIN events e ON e.id=m.event_id WHERE m.id=? AND m.campaign=? AND a.entity_id=? AND e.undone=0''',
                (record,campaign,entity)).fetchone()
            return bool(row and (as_of is None or row['seq']<=as_of))
