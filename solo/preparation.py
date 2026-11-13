"""Local GM sources and unfinished creation drafts; no provider calls or player spoilers."""
import copy,hashlib,io,json,re,sqlite3,time,uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from pydantic import Field
from .contracts import Strict,Hero,Master
from .world import ScenarioPackage,WorldLocation,WorldNode,WorldNPC,WorldRoute,WorldEvent,WorldSettings,ensure,observe

MAX_SOURCE=8*1024*1024
def text_structure(text,source):
    """Explicit author markup only. Free prose is indexed without invented entities."""
    pattern=r'^(?:#{1,6}\s*)?(Старт|Локация|NPC|Дорога|Событие):\s*([^\n]{1,160})\n'
    matches=list(re.finditer(pattern,text,re.M|re.I));locations={};nodes=[];npcs=[];events=[];routes=[];opening=None
    for no,m in enumerate(matches):
        kind=m[1].lower();name=m[2].strip();body=text[m.end():matches[no+1].start() if no+1<len(matches) else len(text)].strip()
        if kind in ('старт','локация'):
            if name in locations:raise ValueError('Повтор локации в разметке источника')
            if kind=='старт':
                if opening:raise ValueError('Две стартовые сцены; оставьте одну')
                opening=dict(name=name,text=body[:3000])
            public=re.search(r'^Описание:\s*(.*)$',body,re.M|re.I)
            locations[name]=WorldLocation(name=name,description=body[:3000] if kind=='старт' else (public[1][:3000] if public else ''),secrets=[body[:1000]] if body and kind!='старт' else [],known_to_hero=kind=='старт')
            nodes.append(WorldNode(id='source-location-'+str(len(nodes)),name=name,coordinates=[2+(len(nodes)%10)*2,2+(len(nodes)//10)*2],known=kind=='старт'))
        elif kind=='npc':
            if ' @ ' not in name:raise ValueError('NPC: Имя @ Локация — нужна явная привязка')
            person,place=name.split(' @ ',1);public=re.search(r'^Описание:\s*(.*)$',body,re.M|re.I)
            npcs.append((place,WorldNPC(name=person,character=public[1] if public else '',private_knowledge=[body[:1000]] if body else [])))
        elif kind=='дорога':
            if ' -> ' not in name:raise ValueError('Дорога: Локация -> Локация — нужны две явные ссылки')
            routes.append((name.split(' -> ',1),json.loads(body)))
        elif kind=='событие':events.append(WorldEvent.model_validate({'id':name,**json.loads(body)}))
    for place,npc in npcs:
        if place not in locations:raise ValueError('NPC ссылается на отсутствующую локацию')
        locations[place].npc.append(npc)
    node_ids={n.name:n.id for n in nodes};resolved=[]
    for pair,config in routes:
        if any(p not in node_ids for p in pair):raise ValueError('Дорога ссылается на отсутствующую локацию')
        resolved.append(WorldRoute.model_validate({'from':node_ids[pair[0]],'to':node_ids[pair[1]],**config}))
    package=ScenarioPackage(name='Источник с явной разметкой',source=source,locations=list(locations.values()),nodes=nodes,routes=resolved,events=events) if locations else None
    return package,opening
class Draft(Strict):
    flow: Literal['legacy','simple']='legacy'
    description: str=Field(default='',max_length=12000)
    name: str=Field(default='Новая хроника',min_length=1,max_length=100)
    source_kind: Literal['ready','custom','sandbox','mixed']='sandbox'
    adventure_bundle: str=Field(default='',max_length=80)
    adventure_module: str=Field(default='',max_length=80)
    adventure_standalone: bool=False
    ruleset: str=Field(default='dnd2024',max_length=80)
    ruleset_version: int | None=Field(default=None,ge=1)
    master_profile: str=Field(default='personal-gm-v1.1',max_length=80)
    world_mode: Literal['story','sandbox','mixed']='sandbox'
    atmosphere: str=Field(default='Мрачное фэнтези',max_length=300)
    opening: str=Field(default='',max_length=3000)
    # An unnamed opening place is a real state, not a missing default: the player
    # is already in dialogue and the Master proposes where the story begins.
    start_name: str=Field(default='',max_length=120)
    questions: list[dict]=Field(default_factory=list,max_length=12)
    prepared: bool=False
    step: int=Field(default=0,ge=0,le=7)
    hero: dict=Field(default_factory=dict)
    equipment: list[str]=Field(default_factory=list,max_length=20)
    wallet_cp: int=Field(default=0,ge=0,le=1000000000)
    background_confirmed: bool=False
    # Отметка прежнего мастера героя. Новый договор ею не пользуется, но черновики,
    # сохранённые до него, её несут; строгий разбор отказывал всему черновику
    # («Extra inputs are not permitted»), и подтверждение мира падало 409 —
    # игрок снова оставался на экране подготовки.
    hero_confirmed: bool=False
    world_confirmation: dict=Field(default_factory=dict)
    def bounded(self):
        if len(json.dumps(self.model_dump(),ensure_ascii=False))>50000:raise ValueError('Черновик слишком велик')
        return self

def pdf_pages(raw):
    from pypdf import PdfReader,apply_configuration
    bound=12*1024*1024
    with apply_configuration(maximum_declared_stream_length=bound,array_based_stream_maximum_output_length=bound,zlib_maximum_output_length=bound,lzw_maximum_output_length=bound,run_length_maximum_output_length=bound,jbig2_maximum_output_length=bound):
        reader=PdfReader(io.BytesIO(raw),strict=True,root_object_recovery_limit=5000)
        if reader.is_encrypted:raise ValueError('Зашифрованный PDF: сначала сохраните доступную копию')
        if len(reader.pages)>300:raise ValueError('PDF превышает 300 страниц')
        pages=[];total=0
        for no,page in enumerate(reader.pages,1):
            stream=page.get_contents()
            if stream and len(stream.get_data())>bound:raise ValueError('Слишком сложная страница PDF')
            text=page.extract_text() or '';total+=len(text)
            if total>1000000:raise ValueError('Текст источника превышает миллион символов')
            pages.append((no,text))
        return pages

def extract(filename,raw):
    suffix=Path(filename).suffix.lower();pages=[];package=None
    if len(raw)>MAX_SOURCE:raise ValueError('Источник превышает 8 МБ')
    if suffix=='.pdf':
        pages=pdf_pages(raw)
    elif suffix in ('.txt','.md','.json'):
        try:text=raw.decode('utf-8-sig')
        except UnicodeError:
            try:text=raw.decode('cp1251')
            except UnicodeError:raise ValueError('Не удалось прочитать кодировку текста')
        if len(text)>1000000:raise ValueError('Текст источника превышает миллион символов')
        if suffix=='.json':
            obj=json.loads(text);package=ScenarioPackage.model_validate(obj.get('package',obj))
        pages=[(1,text)]
    else:raise ValueError('Поддерживаются PDF, TXT, Markdown и JSON сценария')
    text='\n'.join(t for _,t in pages);letters=sum(c.isalpha() for c in text);empty=sum(len(t.strip())<30 for _,t in pages)
    usable=letters>=40 and '\x00' not in text and text.count('\ufffd')<max(3,len(text)//100)
    quality=dict(status='usable' if usable else 'failed',pages=len(pages),empty_pages=empty,characters=len(text),warnings=[])
    if not usable:quality['warnings'].append('Читаемый текст не извлечён. Возможен скан; OCR здесь не выполняется. Подготовка по этому источнику заблокирована.')
    elif empty:quality['warnings'].append('Есть страницы без достаточного текста; полнота документа не подтверждена.')
    headings=re.findall(r'^\s*#{1,6}\s+(.{1,160})$',text,re.M)
    quality['sections']=len(headings)
    opening=None
    if not package:package,opening=text_structure(text,'SHA256:'+hashlib.sha256(raw).hexdigest())
    if opening:quality['public_start']=opening
    if package:
        quality['structure']='structured';quality['locations']=len(package.locations);quality['npc']=sum(len(p.npc) for p in package.locations);quality['events']=len(package.events)
    else:
        quality['structure']='text_index';quality['warnings'].append('Текст сохранён и проиндексирован. Без разметки стартовой сцены или JSON-сценария NPC, события и маршруты не назначаются автоматически.')
    return pages,quality,package

class Preparation:
    def __init__(self,data,store):
        self.path=Path(data)/'preparation.sqlite3';self.store=store
        with self.connection() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,body TEXT NOT NULL,revision INTEGER NOT NULL,updated REAL NOT NULL,campaign TEXT);
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,draft TEXT NOT NULL REFERENCES drafts(id),filename TEXT NOT NULL,sha256 TEXT NOT NULL,original BLOB NOT NULL,quality TEXT NOT NULL,package TEXT,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY,source TEXT NOT NULL REFERENCES sources(id),page INTEGER NOT NULL,offset INTEGER NOT NULL,heading TEXT NOT NULL,content TEXT NOT NULL,scope TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS source_index USING fts5(content,content='chunks',content_rowid='id',tokenize='unicode61');''')
            if 'active' not in {r[1] for r in c.execute('PRAGMA table_info(sources)')}:c.execute('ALTER TABLE sources ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
    @contextmanager
    def connection(self):
        c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:yield c
        finally:c.close()
    def create(self):
        id=str(uuid.uuid4())
        with self.connection() as c:c.execute('INSERT INTO drafts VALUES(?,?,?,?,NULL)',(id,Draft().model_dump_json(),0,time.time()))
        return self.get(id)
    def get(self,id):
        with self.connection() as c:
            row=c.execute('SELECT * FROM drafts WHERE id=?',(id,)).fetchone()
            if not row:raise KeyError('Черновик не найден')
            sources=[dict(id=s['id'],format=Path(s['filename']).suffix[1:],sha256=s['sha256'],quality=json.loads(s['quality'])) for s in c.execute('SELECT id,filename,sha256,quality FROM sources WHERE draft=? AND active=1',(id,))]
            return dict(id=id,body=json.loads(row['body']),revision=row['revision'],updated=row['updated'],campaign=row['campaign'],sources=sources)
    def list(self):
        with self.connection() as c:return [dict(id=r['id'],name=json.loads(r['body'])['name'],updated=r['updated'],campaign=r['campaign']) for r in c.execute('SELECT * FROM drafts ORDER BY updated DESC LIMIT 100')]
    def save(self,id,body,revision):
        body.bounded()
        if body.prepared and body.ruleset_version is None:
            from .rulesets import RulesetRepository
            old=self.get(id)['body'];body.ruleset_version=old.get('ruleset_version') if old['ruleset']==body.ruleset and old.get('ruleset_version') else RulesetRepository(self.store).get(body.ruleset).version
        with self.connection() as c:
            if c.execute('UPDATE drafts SET body=?,revision=revision+1,updated=? WHERE id=? AND revision=? AND campaign IS NULL',(body.model_dump_json(),time.time(),id,revision)).rowcount!=1:raise ValueError('Черновик изменился или уже запущен; откройте его заново')
        return self.get(id)
    def add_source(self,id,filename,raw):
        before=self.get(id)
        if before['campaign']:raise ValueError('Уже начатая кампания не принимает источники в этот черновик')
        try:pages,quality,package=extract(filename,raw)
        except __import__('pydantic').ValidationError:raise ValueError('Структура источника не соответствует поддерживаемой схеме; закрытое содержимое не показано') from None
        except Exception as exc:
            if isinstance(exc,ValueError):raise
            raise ValueError('Источник не удалось прочитать. Пересохраните PDF или загрузите TXT/Markdown.') from None
        sha=hashlib.sha256(raw).hexdigest()
        with self.connection() as c:
            previous=c.execute('SELECT id,active FROM sources WHERE draft=? AND sha256=?',(id,sha)).fetchone()
            if previous:
                if not previous['active']:
                    body=Draft.model_validate(before['body']);body.prepared=False
                    c.execute('UPDATE sources SET active=1 WHERE id=?',(previous['id'],));c.execute('UPDATE drafts SET body=?,revision=revision+1,updated=? WHERE id=?',(body.model_dump_json(),time.time(),id));c.commit()
                return self.get(id)
            count,size=c.execute('SELECT count(*),coalesce(sum(length(original)),0) FROM sources WHERE draft=?',(id,)).fetchone()
            if count>=10 or size+len(raw)>32*1024*1024:raise ValueError('Лимит черновика: 10 источников, 32 МБ')
            sid=str(uuid.uuid4());c.execute('INSERT INTO sources(id,draft,filename,sha256,original,quality,package,created) VALUES(?,?,?,?,?,?,?,?)',(sid,id,Path(filename).name[:200],sha,raw,json.dumps(quality,ensure_ascii=False),package.model_dump_json(by_alias=True) if package else None,time.time()))
            for page,text in pages:
                heading='';public=False
                for offset in range(0,len(text),1200):
                    fragment=text[offset:offset+1200]
                    found=re.findall(r'^\s*#{1,6}\s+(.{1,160})$',fragment,re.M)
                    if found:heading=found[-1]
                    # Raw source pages always GM-only. Public context is published separately by confirmation.
                    row=c.execute('INSERT INTO chunks(source,page,offset,heading,content,scope) VALUES(?,?,?,?,?,?)',(sid,page,offset,heading,fragment,'gm')).lastrowid
                    c.execute('INSERT INTO source_index(rowid,content) VALUES(?,?)',(row,fragment))
            body=Draft.model_validate(before['body'])
            if not body.opening and quality.get('public_start'):
                body.opening=quality['public_start']['text'];body.start_name=quality['public_start']['name']
            body.prepared=False
            c.execute('UPDATE drafts SET body=?,revision=revision+1,updated=? WHERE id=?',(body.model_dump_json(),time.time(),id))
        return self.get(id)
    def add_library_source(self,id,filename,path,library,jobs):
        """A source above the draft limit is read page by page by the library.

        The draft limit keeps a whole book out of the draft database and out of
        this process's memory; it was never a reason to refuse the book. The page
        library streams the file in chunks, extracts it in a separate process and
        stores every page on disk, so the draft only points at the bundle that now
        holds the material.
        """
        from .adventure_library import AUTO_SPLIT_KEY
        draft=self.get(id)
        if draft['campaign']:raise ValueError('Уже начатая кампания не принимает источники в этот черновик')
        body=Draft.model_validate(draft['body']);bundle=body.adventure_bundle
        if bundle:
            try:library.get(bundle)
            except KeyError:bundle=''
        if not bundle:
            title=(Path(filename).stem.strip() or 'Источник приключения')[:150]
            bundle=library.create(title,{AUTO_SPLIT_KEY:True,'rights':'user_private','draft':id})['id']
        job=library.enqueue(bundle,Path(path),Path(filename).name,'book');jobs.submit(job['id'])
        body.adventure_bundle=bundle;body.prepared=False
        with self.connection() as c:
            if c.execute('UPDATE drafts SET body=?,revision=revision+1,updated=? WHERE id=? AND campaign IS NULL',(body.model_dump_json(),time.time(),id)).rowcount!=1:raise ValueError('Черновик изменился или уже запущен; откройте его заново')
        return dict(path='library',job=job['id'],bundle=bundle)

    def bind_library_module(self,id):
        """Keep the module this book was cut into on the draft itself.

        The hero step and the campaign start read the same draft, so the module is
        stored there instead of being remembered by whichever screen uploaded the
        file. A book whose chapters could not be told apart is not bound: its only
        module is one page range over the whole document and says in `notes` that
        no boundary was found, and the game must not claim a scene it never found.
        """
        draft=self.get(id)
        if draft['campaign']:raise ValueError('Кампания уже начата')
        body=Draft.model_validate(draft['body'])
        if not body.adventure_bundle:return dict(bound=False,bundle='',module='',modules=0,pages=[],reasons=['Черновик не ссылается на сборник: сначала загрузите книгу'])
        from .adventure_library import AdventureLibrary
        library=AdventureLibrary(self.store.path.parent)
        try:modules=library.modules(body.adventure_bundle)
        except KeyError:return dict(bound=False,bundle=body.adventure_bundle,module='',modules=0,pages=[],reasons=['Сборник не найден в библиотеке'])
        # Приложение книги — не эпизод: даже с готовыми местами оно не открывает кампанию.
        playable=[m for m in modules if m.get('kind')!='appendix' and m['ready']]
        chosen=next((m for m in playable if m['id']==body.adventure_module),None) or (playable[0] if playable else None)
        if chosen is None:
            reason=(modules[0]['notes'][0] if modules and modules[0]['notes'] else 'Книга прочитана, но границы глав не найдены')
            return dict(bound=False,bundle=body.adventure_bundle,module='',modules=len(modules),pages=[],reasons=[reason+'. Кампания начнётся без книги.'])
        if not chosen['source_pages'] or not chosen['source_pages'][0] or not chosen['source_pages'][0][0]:
            return dict(bound=False,bundle=body.adventure_bundle,module='',modules=len(modules),pages=[],reasons=['Модуль книги не называет ни одной страницы. Кампания начнётся без книги.'])
        if body.adventure_module!=chosen['id']:
            body.adventure_module=chosen['id'];body.prepared=False
            self.save(id,body,draft['revision'])
        return dict(bound=True,bundle=body.adventure_bundle,module=chosen['id'],title=chosen['title'],pages=chosen['source_pages'],modules=len(modules),reasons=[])

    def retrieve(self,campaign,query,*,audience='hero',limit=4):
        if audience not in ('hero','gm'):raise ValueError('Неизвестная аудитория')
        terms=re.findall(r'[^\W_]{2,40}',query,re.U)[:12]
        if not terms:return []
        match=' OR '.join('"'+t.replace('"','')+'"' for t in terms)
        with self.connection() as c:
            rows=c.execute('''SELECT chunks.*,sources.sha256 FROM source_index JOIN chunks ON chunks.id=source_index.rowid JOIN sources ON sources.id=chunks.source JOIN drafts ON drafts.id=sources.draft WHERE source_index MATCH ? AND drafts.campaign=? AND sources.active=1 AND (?='gm' OR chunks.scope='hero') ORDER BY rank LIMIT ?''',(match,campaign,audience,max(1,min(limit,6)))).fetchall()
            return [dict(source=r['source'],sha256=r['sha256'],page=r['page'],offset=r['offset'],text=r['content'][:1000],scope=r['scope']) for r in rows]
    def exclude_source(self,id,source):
        draft=self.get(id)
        if draft['campaign']:raise ValueError('Кампания уже начата')
        body=Draft.model_validate(draft['body']);body.prepared=False
        with self.connection() as c:
            if c.execute('UPDATE sources SET active=0 WHERE id=? AND draft=? AND active=1',(source,id)).rowcount!=1:raise ValueError('Активный источник не найден')
            c.execute('UPDATE drafts SET body=?,revision=revision+1,updated=? WHERE id=?',(body.model_dump_json(),time.time(),id))
        return self.get(id)
    def launch(self,id,confirm):
        if not confirm:raise ValueError('Подтвердите подготовку кампании и лист героя')
        draft=self.get(id)
        # Recovery of a committed campaign if the separate draft DB update was interrupted.
        with self.store.connection() as c:
            existing=c.execute("SELECT id FROM campaigns WHERE json_extract(state,'$.preparation.draft')=?",(id,)).fetchone()
        if existing:
            with self.connection() as c:c.execute('UPDATE drafts SET campaign=?,updated=? WHERE id=?',(existing['id'],time.time(),id))
            return self.store.campaign(existing['id'])
        body=Draft.model_validate(draft['body'])
        character=None
        if body.flow=='simple':
            if not hasattr(self,'character_creation'):raise ValueError('Сервис проверки героя недоступен')
            character=self.character_creation.confirmed_preview(id,body)
        try:hero=Hero.model_validate(body.hero)
        except __import__('pydantic').ValidationError as exc:raise ValueError('Проверьте поля листа героя: '+', '.join('.'.join(map(str,e['loc'])) for e in exc.errors()[:6])) from None
        if not body.prepared or not body.background_confirmed:raise ValueError('Подтвердите подготовку и отдельный шаг предыстории')
        if body.source_kind in ('ready','custom') and not draft['sources'] and not body.adventure_bundle:raise ValueError('Добавьте источник или выберите песочницу')
        if any(s['quality']['status']!='usable' for s in draft['sources']):raise ValueError('Источник не прочитан; замените его или создайте новый черновик без этого файла')
        if not body.opening.strip():raise ValueError('Укажите открытую стартовую сцену; закрытый текст не публикуется автоматически')
        from .rulesets import RulesetRepository,RulesetEngine,active
        # The draft's ruleset is the edition the book was written for. The campaign
        # runs on the player's mechanics preset, exactly as the Native path does:
        # taking the book's edition as the preset put a second calculator on the
        # game and made a prepared 2014 scenario disagree with its own compiled
        # definition, which the engine then refused to open at all.
        preset=RulesetRepository(self.store).get(body.ruleset,body.ruleset_version)
        mechanics=active({})
        if (preset.id,preset.version)!=(mechanics.id,mechanics.version):
            preset=mechanics
        effects=RulesetEngine(preset).exhaustion_effects(hero)
        if effects['mechanical_death'] or hero.hp>effects['effective_max_hp']:raise ValueError('Хиты и истощение не соответствуют правилам')
        profiles=self.store.profiles();profile=next((p for p in profiles if p['id']==body.master_profile),None)
        if not profile:raise ValueError('Выберите сохранённый профиль Мастера')
        # The draft may leave the opening unnamed on purpose: the material never
        # printed it and the first game turn asks for it. A campaign still needs a
        # scene name, so it says so instead of failing to launch on an empty string.
        start_name=str(body.start_name or '').strip() or 'Место начала ещё не названо'
        state=dict(location=start_name,turn=0,locations=[WorldLocation(name=start_name,description=body.opening,known_to_hero=True).model_dump()],facts=[],deferred=[],geography={'nodes':[WorldNode(id='start',name=start_name,coordinates=[1,1]).model_dump()],'routes':[]},world={'settings':WorldSettings(mode=body.world_mode,map_travel=False).model_dump()},hero=hero.model_dump(),ruleset=preset.model_dump(),preparation=dict(draft=id,source_count=len(draft['sources']),atmosphere=body.atmosphere,background_confirmed=True))
        if character:
            state['economy']=character['economy'];state['character_creation']=dict(source=character['source'],catalog_version=character['catalog_version'],magic=character['magic'],warnings=character['warnings'])
            state['preparation']['public_description']=body.description[:500]
        ensure(state)
        from .world import import_package
        with self.connection() as c:
            packages=[ScenarioPackage.model_validate_json(r[0]) for r in c.execute('SELECT package FROM sources WHERE draft=? AND active=1 AND package IS NOT NULL',(id,))]
        adventure_library=definition=None
        if body.adventure_bundle:
            from .adventure_library import AdventureLibrary
            adventure_library=AdventureLibrary(self.store.path.parent);definition=adventure_library.definition(body.adventure_bundle,body.adventure_module or None)
            if definition.world:packages.append(definition.world)
        for package in packages:
            package=package.model_copy(deep=True)
            for place in package.locations:
                for npc in place.npc:
                    private=[*npc.private_knowledge,*npc.goals,*([npc.motivation] if npc.motivation else []),*([npc.biography] if npc.biography else []),*([npc.known] if npc.known else [])]
                    npc.private_knowledge=[v[:1000] for v in private[:20]];npc.goals=[];npc.motivation='';npc.biography='';npc.known=''
            # Start already supplied publicly by author; structured data may extend it explicitly.
            if any(p.name==body.start_name for p in package.locations):
                replacement=next(p for p in package.locations if p.name==body.start_name)
                replacement=WorldLocation.model_validate({**replacement.model_dump(),'description':body.opening,'known_to_hero':True})
                state['locations'][0]=replacement.model_dump();package=package.model_copy(deep=True)
                package.locations=[p for p in package.locations if p.name!=body.start_name]
                start_node=next((n for n in package.nodes if n.name==body.start_name),None)
                if start_node:state['geography']['nodes'][0]=start_node.model_dump();package.nodes=[n for n in package.nodes if n.name!=body.start_name]
            import_package(state,package)
        from .inventory import InventoryEngine,CATALOG,new_item
        inventory=InventoryEngine(state)
        for catalog in body.equipment:
            if catalog not in CATALOG:raise ValueError('Неизвестная начальная экипировка')
            inventory.data.items.append(new_item(catalog,1,'hero','Начальное снаряжение подтверждено игроком'))
        inventory.data.wallet_cp=body.wallet_cp;inventory.assert_capacity();inventory.flush();observe(state)
        if definition:
            from .adventure_engine import initialize
            initialize(state,body.adventure_bundle,definition,adventure_library,standalone=body.adventure_standalone)
        master=Master.model_validate(profile['master']);master.literary.theme=body.atmosphere
        result=self.store.create(body.name,hero,preset,initial_state=state,master=master)
        with self.connection() as c:c.execute('UPDATE drafts SET campaign=?,updated=? WHERE id=?',(result['id'],time.time(),id))
        return result

def register_preparation_api(app,store,game):
    from fastapi import UploadFile,File,HTTPException
    from .combat_view import public_campaign
    service=Preparation(store.path.parent,store);app.state.preparation=service;game.preparation=service
    class SaveDraft(Strict):
        body: Draft
        revision: int=Field(ge=0)
    class Launch(Strict):confirm: bool=False
    @app.post('/api/preparation/drafts/{id}/sheet')
    def sheet(id:str):
        from .rulesets import RulesetRepository,RulesetEngine
        body=Draft.model_validate(service.get(id)['body'])
        try:hero=Hero.model_validate(body.hero)
        except __import__('pydantic').ValidationError as exc:raise ValueError('Проверьте поля листа героя: '+', '.join('.'.join(map(str,e['loc'])) for e in exc.errors()[:6])) from None
        return RulesetEngine(RulesetRepository(store).get(body.ruleset,body.ruleset_version)).sheet(hero)
    @app.get('/api/preparation/options')
    def options():
        from .rulesets import RulesetRepository,SKILLS,SKILL_NAMES
        from .inventory import CATALOG
        return dict(hero=Hero(name='Новый герой').model_dump(),rulesets=RulesetRepository(store).list(),profiles=[dict(id=p['id'],name=p['name']) for p in store.profiles()],skills={k:dict(name=SKILL_NAMES[k],ability=v) for k,v in SKILLS.items()},items=[dict(id=k,name=v['name'],weight_centilb=v['weight_centilb'],value_cp=v['value_cp']) for k,v in CATALOG.items()],coverage='Проверки, навыки, владение, истощение, отдых, зарегистрированное снаряжение и базовый тактический бой. Класс, подкласс, вид, происхождение и список заклинаний — ручные записи; полного SRD и автоматического развития нет.')
    @app.get('/api/preparation/drafts')
    def drafts():return service.list()
    @app.post('/api/preparation/drafts')
    async def new():
        async with game.operation:return service.create()
    @app.get('/api/preparation/drafts/{id}')
    def get(id:str):return service.get(id)
    @app.put('/api/preparation/drafts/{id}')
    async def save(id:str,body:SaveDraft):
        async with game.operation:
            if service.get(id)['body']['flow']=='simple' or body.body.flow=='simple':raise ValueError('Обычный черновик изменяется через подготовку приключения и проверяемое создание героя')
            return service.save(id,body.body,body.revision)
    @app.post('/api/preparation/drafts/{id}/sources')
    async def source(id:str,file:UploadFile=File(...)):
        from starlette.concurrency import run_in_threadpool
        name=file.filename or 'source.txt';raw=await file.read(MAX_SOURCE+1)
        if len(raw)<=MAX_SOURCE:
            await file.close()
            async with game.operation:return await run_in_threadpool(service.add_source,id,name,raw)
        # Above the draft limit the file is not refused: it is handed to the page
        # library, which reads it in chunks in its own worker process. The first
        # MAX_SOURCE+1 bytes are already in memory; the rest never is.
        from .adventure_library import MAX_FILE
        library=getattr(app.state,'adventures',None);jobs=getattr(app.state,'adventure_jobs',None)
        if library is None or jobs is None:raise ValueError('Библиотека приключений недоступна в этом запуске')
        temporary=library.files/('.upload-'+uuid.uuid4().hex+'.part');size=len(raw)
        try:
            with temporary.open('wb') as stream:
                await run_in_threadpool(stream.write,raw)
                while True:
                    chunk=await file.read(1024*1024)
                    if not chunk:break
                    size+=len(chunk)
                    if size>MAX_FILE:raise ValueError('Файл превышает предел 384 МиБ. Оригинал на вашем диске не изменён.')
                    await run_in_threadpool(stream.write,chunk)
            async with game.operation:return await run_in_threadpool(service.add_library_source,id,name,temporary,library,jobs)
        finally:
            await file.close()
            if temporary.exists():temporary.unlink()
    @app.post('/api/preparation/drafts/{id}/world')
    async def world(id:str):
        async with game.operation:return service.bind_library_module(id)
    @app.post('/api/preparation/drafts/{id}/launch')
    async def launch(id:str,body:Launch):
        # r210: прежний мастер начинал кампанию с ручным калькулятором и
        # состоянием без признака Nat20 — такой путь закрыт целиком.
        from .command_backend import creation_refusal
        raise HTTPException(409,creation_refusal())
    @app.post('/api/preparation/drafts/{id}/sources/{source}/exclude')
    async def exclude(id:str,source:str):
        async with game.operation:return service.exclude_source(id,source)
