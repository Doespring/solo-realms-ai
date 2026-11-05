"""On-demand, cancellable Qwen preparation with persisted progress and private results."""
import asyncio,hashlib,json,time,uuid
from builtins import id as object_id
from contextlib import aclosing
from .preparation_contracts import PrepareRequest,PreparationBatch,PreparationResumeMetadata
from .prepared_world import PreparedWorldValidator
from .preparation import Draft

class PreparationProblem(ValueError):pass


def active_mechanics():
    """The mechanics preset this build actually executes.

    The book's edition and the mechanics edition are two separate facts. Prepared
    scenarios are always written against the preset Nat20 runs here, so a 2014 book
    is prepared for 2024 numbers and the book's own edition is recorded next to it.
    """
    from .rulesets import BUILTINS
    return BUILTINS['dnd2024'].id


EDITION_MARKERS={'dnd2014':('пятой редакции','пятая редакция','5-я редакция','5 редакция','d&d 2014','днд 2014','srd 5.1'),
    'dnd2024':('шестой редакции','шестая редакция','6-я редакция','d&d 2024','днд 2024','srd 5.2')}


def edition_evidence(library,documents):
    """Cite the wording a book uses about its own edition; never a bare absence.

    The absence of "2024" proves nothing, so only an explicit marker counts as a
    hit. Every hit is reported with its physical page, and the result stays
    unknown when nothing decisive is printed or when both editions are claimed.
    This is evidence about the source text, kept apart from the mechanics a
    player later chooses for the hero.
    """
    if not documents:return dict(edition=None,ambiguous=False,markers=[],pages=[])
    hits=[]
    with library.db() as db:
        for document in documents:
            for number,content in db.execute('SELECT number,content FROM pages WHERE document=? ORDER BY number',(document,)):
                text=' '.join((content or '').casefold().replace('ё','е').split())
                for edition,markers in EDITION_MARKERS.items():
                    for marker in markers:
                        count=text.count(marker)
                        if count:hits.append(dict(edition=edition,marker=marker,page=number,count=count))
    claimed={h['edition'] for h in hits}
    if len(claimed)!=1:
        return dict(edition=None,ambiguous=len(claimed)>1,markers=hits,pages=sorted({h['page'] for h in hits}))
    edition=next(iter(claimed))
    return dict(edition=edition,ambiguous=False,markers=hits,pages=sorted({h['page'] for h in hits}))


def detect_edition(library,documents):
    """The single edition the source text explicitly claims, else None.

    Only decisive wording counts; an unclear or contradictory source stays
    unknown and the player is asked.
    """
    return edition_evidence(library,documents)['edition']


def book_fingerprint(identity):
    """Отпечаток исходной книги — без черновика и без описания.

    identity включает draft и description, поэтому та же книга во втором
    черновике с другим замыслом получала новый отпечаток и оплачивалась
    заново. Книгу определяет текст, редакция, промпт и модель; замысел
    игрока меняет стартовую сцену, но не сам разбор источника.
    """
    stable={key:value for key,value in identity.items() if key not in ('draft','description')}
    parameters=stable.get('parameters') or {}
    stable['parameters']={key:value for key,value in parameters.items() if key not in ('price_checked_at','input_per_million','output_per_million','image_per_million')}
    return hashlib.sha256(json.dumps(stable,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def resume_metadata(result):
    """Whitelist only explicit persisted mode and edition; never infer from progress."""
    result=result if isinstance(result,dict) else {}
    metadata=result.get('preparation_metadata')
    if isinstance(metadata,dict):
        mode=metadata.get('preparation_mode','unknown')
        source=metadata.get('source_ruleset')
    else:
        contract=result.get('start_contract')
        flag=contract.get('world_structure') if isinstance(contract,dict) else None
        mode='world_structure' if flag is True else 'source_extract' if flag is False else 'unknown'
        editions={d.get('ruleset') for d in (result.get('definition'),result.get('full_definition'))
                  if isinstance(d,dict) and d.get('ruleset') in ('dnd2014','dnd2024')}
        source=next(iter(editions)) if len(editions)==1 else None
    return PreparationResumeMetadata(
        preparation_mode=mode if mode in ('world_structure','source_extract') else 'unknown',
        source_ruleset=source if source in ('dnd2014','dnd2024') else None).model_dump()

#: The world-structure instruction is part of what the model is asked, so it is
#: hashed with the prompt: changing a sentence here must invalidate the cached
#: blocks instead of replaying an answer to a question that is no longer asked.
WORLD_INSTRUCTION=('Подготовь структуру мира по дословным источникам: открытое описание только из read-aloud, '
    'NPC с явной локацией и знаниями, подтверждёнными цитатой, маршруты только с подтверждёнными концами. '
    'Каждое место, которое страница называет отдельно, — отдельная location, даже если описана одной фразой; '
    'развилку, названную в прозе, переноси как маршрут между этими местами. '
    'Противник упомянут — это opponent, даже без статблока: кто охраняет, кто нападает, кто угрожает. '
    'Каждая награда с числом на странице — reward, число клади в data.experience; строку таблицы наград тоже переноси. '
    'Упоминание противника не делает бой обязательным: условие мира переноси цитатой. '
    'Перечисли всё, что страница называет, а не только то, что описано подробно. '
    'Не выдавай список имён за готовую сцену. Не выдумывай связи, знания, параметры боя или обязательные зависимости. '
    'Если исходных данных недостаточно, укажи problems. Сохрани редакцию источника.')

class PreparationPipeline:
    def __init__(self,creation,llm,store):
        self.creation=creation;self.library=creation.library;self.preparation=creation.preparation;self.llm=llm;self.store=store;self.tasks={}
        self.preparation.preparation_pipeline=self
        with self.preparation.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS creation_runs(id TEXT PRIMARY KEY,draft TEXT NOT NULL REFERENCES drafts(id),request TEXT NOT NULL,fingerprint TEXT NOT NULL,status TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,message TEXT NOT NULL DEFAULT "",result TEXT,created REAL NOT NULL,book TEXT NOT NULL DEFAULT "",UNIQUE(draft,request))')
            # Отпечаток книги без номера черновика: по нему видно, что этот
            # источник уже разобран в другом черновике, и платить второй раз
            # за тот же текст не нужно.
            if 'book' not in {r['name'] for r in db.execute('PRAGMA table_info(creation_runs)')}:db.execute('ALTER TABLE creation_runs ADD COLUMN book TEXT NOT NULL DEFAULT ""')
            db.execute('CREATE INDEX IF NOT EXISTS creation_runs_book ON creation_runs(book)')
            db.execute('CREATE TABLE IF NOT EXISTS creation_batch_history(run TEXT NOT NULL REFERENCES creation_runs(id),batch INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(run,batch))')
            db.execute('CREATE TABLE IF NOT EXISTS creation_parts(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,source TEXT NOT NULL,status TEXT NOT NULL DEFAULT "queued",body TEXT,raw TEXT,diagnostic TEXT NOT NULL DEFAULT "[]",metric TEXT NOT NULL DEFAULT "{}",children TEXT NOT NULL DEFAULT "[]",attempts INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL)')
            if 'signature' not in {r['name'] for r in db.execute('PRAGMA table_info(creation_parts)')}:db.execute('ALTER TABLE creation_parts ADD COLUMN signature TEXT NOT NULL DEFAULT ""')
            db.execute('CREATE INDEX IF NOT EXISTS creation_parts_signature ON creation_parts(signature)')
            db.execute('CREATE TABLE IF NOT EXISTS creation_part_links(run TEXT NOT NULL REFERENCES creation_runs(id),part TEXT NOT NULL REFERENCES creation_parts(id),ordinal INTEGER NOT NULL,PRIMARY KEY(run,part))')
            db.execute('CREATE TABLE IF NOT EXISTS creation_part_attempts(part TEXT NOT NULL REFERENCES creation_parts(id),attempt INTEGER NOT NULL,status TEXT NOT NULL,raw TEXT,diagnostic TEXT NOT NULL,metric TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(part,attempt))')
            db.execute('INSERT OR IGNORE INTO creation_part_attempts SELECT id,attempts,status,raw,diagnostic,metric,created FROM creation_parts WHERE attempts>0 AND status IN ("validated","failed","interrupted","split")')
            db.execute('UPDATE creation_parts SET status="interrupted" WHERE status="running"')
            db.execute('UPDATE creation_runs SET status="interrupted",message="Подготовка прервана. Можно безопасно повторить." WHERE status IN ("queued","running")')
    def raw(self,id):
        with self.preparation.connection() as db:row=db.execute('SELECT * FROM creation_runs WHERE id=?',(id,)).fetchone()
        if row is None:raise KeyError('Подготовка не найдена')
        return dict(row)
    def status(self,id):
        row=self.raw(id);result=json.loads(row['result']) if row['result'] else None
        definition=(result or {}).get('definition')
        world=(definition.get('world') or {}) if isinstance(definition,dict) else {}
        metadata=resume_metadata(result)
        # Results contain GM-only annotations and quotes. The player sees counts only.
        with self.preparation.connection() as db:
            errors=[dict(block=r['ordinal']//1000+1,**error) for r in db.execute('SELECT p.diagnostic,l.ordinal FROM creation_parts p JOIN creation_part_links l ON l.part=p.id WHERE l.run=? AND p.status="failed" ORDER BY l.ordinal',(id,)) for error in json.loads(r['diagnostic'])[:2]][:8]
            if metadata['source_ruleset'] is None:
                editions={r[0] for r in db.execute("SELECT DISTINCT json_extract(body,'$.ruleset') FROM creation_batch_history WHERE run=?",(id,)) if r[0] in ('dnd2014','dnd2024')}
                if len(editions)==1:metadata['source_ruleset']=next(iter(editions))
        from collections import Counter
        issues=Counter((q['kind'],q['reason'].split('\n')[0]) for q in (result or {}).get('quarantine',[]))
        # Expose causes, never rejected source values or raw Pydantic inputs.
        safe_reasons=('Цитата не подтверждена страницей','Нет происхождения факта','Неизвестная локация NPC','Механический обработчик этого элемента не зарегистрирован','Повтор имени локации','Текстовое сведение не подтверждено цитатой (поле name)','Стартовая локация не подтверждена','Есть страницы без доступного текста')
        canonical_issues=[dict(kind=k,count=n,reason=reason if reason in safe_reasons else 'Элемент не прошёл проверку канона; подробности сохранены отдельно') for (k,reason),n in issues.items()][:12]
        return dict(id=id,draft=row['draft'],status=row['status'],done=row['done'],total=row['total'],message=row['message'],reused=bool((result or {}).get('reused_from')),ready=bool(row['status']=='ready' and metadata['preparation_mode']!='unknown'),errors=errors,canonical_issues=canonical_issues,text_only=True,entry_readiness=result.get('entry_readiness') if result else None,summary=dict(locations=len(world.get('locations',[])),npc=sum(len(p['npc']) for p in world.get('locations',[])),quarantined=len(result.get('quarantine',[]))) if definition else None,**metadata)

    def apply_result(self,draft,bundle,ruleset,result,*,confirmation=None):
        if not result['ready']:return
        self.library.save_definition(bundle,result['definition'],True)
        current=self.creation.owned(draft);body=Draft.model_validate(current['body'])
        # `ruleset` here is the BOOK's edition, kept apart from the mechanics preset the
        # scenario executes. `apply_result` receives the source edition, and the
        # definition carries its own mechanics preset.
        body.ruleset=ruleset;body.ruleset_version=1;body.name=result['title'];body.prepared=True
        body.adventure_module=result['definition']['id']
        body.world_confirmation=confirmation or {}
        # A material that never named its opening place still yields a playable
        # world: the campaign opens in clarification, the Master proposes a start
        # in dialogue, and nothing is invented behind the player's back.
        start=result.get('start') or ''
        places=(result['definition'].get('world') or {}).get('locations') or []
        place=next((p for p in places if p.get('name')==start),None) if start else None
        body.start_name=start or ''
        body.opening=(place or {}).get('description') or (
            '' if place is None and not start else 'Вы находитесь в месте «'+start+'». Что делает ваш герой?')
        body.questions=list(result.get('questions') or [])
        self.preparation.save(draft,body,current['revision'])

    def world_result(self,draft,run_id=None):
        """Existing validated full world; pinned observation entries never promote it."""
        current=self.creation.owned(draft)
        if run_id is None:
            with self.preparation.connection() as db:
                row=db.execute('SELECT id FROM creation_runs WHERE draft=? ORDER BY created DESC LIMIT 1',(draft,)).fetchone()
            if row is None:raise ValueError('Нет сохранённой подготовки мира.')
            run_id=row[0]
        run=self.raw(run_id)
        if run['draft']!=draft:raise ValueError('Подготовка принадлежит другому черновику.')
        if run['status'] not in ('ready','blocked') or run['done']!=run['total'] or not run['result']:
            raise ValueError(f"Подготовка не завершена: {run['done']} / {run['total']}. Продолжите сохранённые блоки.")
        result=json.loads(run['result']);contract=result.get('start_contract')
        mode=resume_metadata(result)['preparation_mode']
        if mode=='unknown':raise ValueError('Режим сохранённой подготовки неизвестен. Явно выберите структурную подготовку мира.')
        if mode!='world_structure':raise ValueError('Сохранён компактный разбор: имён недостаточно для подтверждения мира. Явно выберите world_structure.')
        if not contract:raise ValueError('Нет проверенной версии структурного старта. Подготовьте структуру мира явно.')
        documents=sorted(self.creation.allowed_documents(draft))
        if contract['documents']!=documents or contract['description_sha256']!=hashlib.sha256(current['body']['description'].encode()).hexdigest():
            raise ValueError('Описание или набор источников изменились. Подготовьте актуальную структуру.')
        if any(not f['job'] or f['job']['status']!='complete' for f in self.creation.get(draft)['files']):
            raise ValueError('Извлечение выбранных источников не завершено.')
        definition=result.get('full_definition',result['definition'])
        # A verified entry scene is a real, playable start: one place, its read-aloud text
        # and an explicit boundary. It is not the whole book, but it must open, otherwise
        # the player pays for a world that can never be entered.
        entry=result.get('definition') if (result.get('definition') or {}).get('entry_stage') else None
        if entry:
            # The entry world publishes only the verified opening place, so the same proof
            # applies to it alone: a named start with its read-aloud text present.
            places=(entry.get('world') or {}).get('locations') or []
            opening=next((p for p in places if p.get('name')==result.get('start')),None)
            if not opening or not opening.get('description','').strip():
                raise ValueError('Подтверждённый вход не содержит открытой стартовой сцены.')
            return current,run_id,result
        reasons=[q['reason'] for q in result['quarantine'] if q.get('essential')]
        world=definition.get('world') or {}
        named=result.get('start') or ''
        # An unnamed opening is not a missing description: nothing was ever named.
        # The world is confirmed so play can begin in clarification, and the place
        # is agreed in dialogue afterwards without paying for the analysis twice.
        start=next((p for p in world.get('locations',[]) if p['name']==named),None) if named else None
        if named and (not start or not start.get('description','').strip()):
            reasons.append('Нет подтверждённого открытого описания стартовой локации; имён недостаточно.')
        if definition.get('entry_stage'):reasons.append('Изолированный observation-only вход не является подтверждённым полным миром.')
        from .prototype_prepared import native_start_blockers
        reasons.extend(native_start_blockers(definition,result.get('start')))
        if not definition.get('reviewed') and not reasons:reasons.append('Мир не прошёл проверку структуры и обязательных зависимостей.')
        if reasons:raise ValueError('Старт мира заблокирован: '+'; '.join(dict.fromkeys(reasons))[:1800])
        if not result['ready']:raise ValueError('Полная структура мира не готова.')
        return current,run_id,result

    def confirm_world(self,draft_id,*,expected_revision,run_id,confirm):
        if not confirm:raise ValueError('Подтвердите проверенную структуру мира.')
        current,run_id,result=self.world_result(draft_id,run_id)
        token=dict(run_id=run_id,definition_sha256=hashlib.sha256(json.dumps(result['definition'],sort_keys=True,ensure_ascii=False).encode()).hexdigest())
        if current['body'].get('world_confirmation')==token and current['body']['prepared']:
            return current
        if current['revision']!=expected_revision:raise ValueError('Черновик изменился. Обновите подтверждение мира.')
        if current['campaign']:raise ValueError('Кампания уже начата.')
        # The draft records the BOOK's edition; the scenario's own `ruleset` is the
        # mechanics preset and must not overwrite it here.
        source_edition=result['definition'].get('source_ruleset') or result['definition']['ruleset']
        self.apply_result(draft_id,current['body']['adventure_bundle'],source_edition,result,confirmation=token)
        return self.preparation.get(draft_id)

    def settle_start(self,draft,*,expected_revision,name):
        """Record the opening place the Master and player agreed in dialogue.

        The source named no such place, so this is an original addition, not a
        quoted fact. It costs nothing: the world is already compiled and the
        place must be one it actually contains.
        """
        name=(name or '').strip()
        if not name:raise ValueError('Назовите место начала.')
        current=self.creation.owned(draft)
        if current['campaign']:raise ValueError('Кампания уже начата.')
        if current['revision']!=expected_revision:raise ValueError('Черновик изменился. Обновите страницу.')
        body=Draft.model_validate(current['body'])
        if body.start_name.strip():
            if body.start_name==name:
                return self.preparation.get(draft)
            raise ValueError('Место начала уже согласовано: '+body.start_name)
        token=body.world_confirmation or {}
        if not token.get('run_id'):raise ValueError('Сначала подтвердите проверенный мир; списка имён недостаточно.')
        _,_,result=self.world_result(draft,token['run_id'])
        places=(result['definition'].get('world') or {}).get('locations') or []
        place=next((p for p in places if (p.get('name') or '').strip().casefold()==name.casefold()),None)
        if place is None:
            raise ValueError('Такого места в подготовленном мире нет. Назовите одно из известных.')
        body.start_name=place['name']
        body.opening=(place.get('description') or '').strip() or ('Вы находитесь в месте «'+place['name']+'». Что делает ваш герой?')
        body.questions=[q for q in body.questions if q.get('field')!='start']
        self.preparation.save(draft,body,current['revision'])
        return self.preparation.get(draft)

    def recheck_start(self,draft):
        """Explicit local-only revalidation: no model configuration or network."""
        private=self.creation.owned(draft);value=self.creation.get(draft)
        if value['campaign']:raise ValueError('Приключение уже начато')
        if any(not f['job'] or f['job']['status']!='complete' for f in value['files']):raise ValueError('Дождитесь извлечения выбранных файлов')
        documents=self.creation.allowed_documents(draft)
        if not documents:raise ValueError('Для локальной проверки старта нужны извлечённые источники')
        with self.preparation.connection() as db:
            if db.execute('SELECT 1 FROM creation_runs WHERE draft=? AND status IN ("queued","running")',(draft,)).fetchone():raise ValueError('Дождитесь завершения текущей подготовки или отмените её')
            run=db.execute('SELECT r.* FROM creation_runs r WHERE draft=? AND EXISTS (SELECT 1 FROM creation_batch_history h WHERE h.run=r.id) ORDER BY created DESC LIMIT 1',(draft,)).fetchone()
            if not run:raise ValueError('Нет сохранённых блоков подготовки. Сначала подготовьте материалы.')
            batches=[json.loads(r[0]) for r in db.execute('SELECT body FROM creation_batch_history WHERE run=? ORDER BY batch',(run['id'],))]
            source_parts=[json.loads(r[0]) for r in db.execute('SELECT p.source FROM creation_parts p JOIN creation_part_links l ON l.part=p.id WHERE l.run=?',(run['id'],))]
        if not batches:raise ValueError('Нет проверенных блоков; повторная отправка книги автоматически не выполняется')
        # Reuse only the same selected source set, never unrelated cached worlds.
        referenced={p['document'] for b in batches for e in b['entities'] for p in e['evidence']}
        expected={p['document'] for part in source_parts for p in part['pages']} or referenced
        if expected!=documents or not referenced<=documents:raise ValueError('Набор источников изменился; сохранённые блоки относятся к другой версии')
        previous=json.loads(run['result']) if run['result'] else {}
        metadata=resume_metadata(previous)
        editions={b['ruleset'] for b in batches if b['ruleset']!='unknown'}
        if metadata['source_ruleset'] is None and len(editions)!=1:raise ValueError('Редакция правил в сохранённых блоках не определена однозначно')
        ruleset=metadata['source_ruleset'] or editions.pop();module='prepared-'+draft.replace('-','')[:24]
        # `ruleset` here is the edition the book was written for. The scenario is
        # prepared for the player's mechanics preset, and the book's own edition is
        # recorded beside it so the two never get confused for one another.
        result=PreparedWorldValidator(self.library,documents).compile(batches,active_mechanics(),
            module=module,complete=run['done']==run['total'],source_ruleset=ruleset)
        from .first_playable import entry_result
        result=entry_result(self.library,documents,result,ruleset,module)
        if metadata['source_ruleset'] is None:metadata['source_ruleset']=ruleset
        # Keep the book's own wording and the chosen mechanics as two separate facts.
        result['edition_evidence']=edition_evidence(self.library,documents)
        metadata['edition_evidence']=dict(edition=result['edition_evidence']['edition'],
            pages=result['edition_evidence']['pages'],ambiguous=result['edition_evidence']['ambiguous'])
        result['preparation_metadata']=metadata
        if previous.get('start_contract'):result['start_contract']=previous['start_contract']
        fingerprint=hashlib.sha256(json.dumps([documents and sorted(documents),batches,'entry-v1'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        id=str(uuid.uuid4());message=(result.get('entry_readiness') or {}).get('reason') or ('Приключение подготовлено.' if result['ready'] else 'Стартовая сцена требует проверки.')
        if metadata['preparation_mode']!='unknown':self.apply_result(draft,private['body']['adventure_bundle'],ruleset,result)
        with self.preparation.connection() as db:
            db.execute('INSERT INTO creation_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)',(id,draft,'local-'+id,fingerprint,'ready' if result['ready'] else 'blocked',run['done'],run['total'],message,json.dumps(result,ensure_ascii=False),time.time(),run['book'] or ''))
            db.execute('INSERT INTO creation_batch_history SELECT ?,batch,body FROM creation_batch_history WHERE run=?',(id,run['id']))
            db.execute('INSERT INTO creation_part_links SELECT ?,part,ordinal FROM creation_part_links WHERE run=?',(id,run['id']))
        return self.status(id)
    def latest(self,draft):
        with self.preparation.connection() as db:row=db.execute('SELECT id FROM creation_runs WHERE draft=? ORDER BY created DESC LIMIT 1',(draft,)).fetchone()
        value=self.status(row[0]) if row else None
        if value and value['status']=='ready' and not self.creation.owned(draft)['body']['prepared']:
            value.update(status='stale',ready=False,message='Описание или материалы изменились. Подготовьте приключение заново.')
        return value
    def update(self,id,**values):
        if not set(values)<={'status','done','total','message','result'}:raise ValueError('Недопустимый прогресс')
        if values.get('result'):
            result=json.loads(values['result']);old=self.raw(id)
            previous=json.loads(old['result']) if old['result'] else {}
            result['preparation_metadata']=resume_metadata(previous)
            values['result']=json.dumps(result,ensure_ascii=False)
        with self.preparation.connection() as db:db.execute('UPDATE creation_runs SET '+','.join(k+'=?' for k in values)+' WHERE id=? AND status!="cancelled"',(*values.values(),id))
    def inputs(self,draft,request):
        from .ai_roles import select_role,prompt as read_prompt
        value=self.creation.get(draft);private=self.creation.owned(draft);documents=self.creation.allowed_documents(draft)
        if value['campaign']:raise ValueError('Приключение уже начато')
        if not value['description'].strip() and not value['files']:raise ValueError('Добавьте материалы или описание приключения')
        # «Дождитесь извлечения» ничего не обещает для файла, который уже упал:
        # он не дождётся. Отдаём причину отказа и что делать вместо него.
        failed=[f for f in value['files'] if f['job'] and f['job']['status']=='failed']
        if failed:
            first=failed[0];reason=(first.get('quality') or {}).get('reason') or 'причина в журнале загрузки'
            raise ValueError('Файл «'+first['name']+'» не извлечён: '+reason+' Уберите его из подготовки или загрузите другой источник.')
        if any(f['job']['status']!='complete' for f in value['files']):raise ValueError('Сначала дождитесь извлечения файлов или исключите ошибочные')
        ruleset=request.ruleset
        if not ruleset:
            import re
            explicit=set(re.findall(r'(?<!\d)(2014|2024)(?!\d)',value['description']))
            if len(explicit)==1:ruleset='dnd'+explicit.pop()
        # The uploaded book usually states its own edition. Reading it is more reliable
        # than asking the player to guess, and the player's explicit choice still wins.
        # Evidence about the source edition is recorded separately from the mechanics
        # a player later picks for the hero, so an unclear book is reported, not hidden.
        evidence=edition_evidence(self.library,documents)
        if not ruleset:ruleset=evidence['edition']
        if not ruleset:
            detail='Текст книги не называет редакцию однозначно.' if documents else 'Материалы не приложены.'
            raise ValueError('Выберите редакцию правил для нового приключения: D&D 2014 или D&D 2024. '+detail+' Существующие кампании не изменятся.')
        if not hasattr(self.llm,'configured'):raise ValueError('AI-подготовка недоступна: подключите облачного Мастера. Тестовый рассказчик не готовит приключения.')
        profile,_=self.llm.configured();cfg,prompt_name=select_role(profile,'preparation_world')
        # An uploaded document is prepared as a world unless the caller explicitly asks for
        # the legacy name list. The player must never face this as a mode question: the old
        # default silently produced names-only runs that could not open any location.
        # Written text is material too. A pasted premise cannot cite pages, but it
        # still asks for a world: the compact name list would give it no places,
        # no opening and no way in. Only an explicit opt-out uses the legacy list.
        world_structure=request.world_structure or 'world_structure' not in request.model_fields_set
        if documents and not world_structure:prompt_name='qwen_source_extract_v1'
        if not request.consent_to_send:raise ValueError('Для AI-подготовки подтвердите отправку выбранного текста настроенному провайдеру. Это отдельное действие по тарифу API.')
        identity=dict(draft=draft,description=value['description'],documents=sorted(documents),ruleset=ruleset,model=cfg.model,provider=profile.base_url,endpoint=cfg.endpoint,prompt=prompt_name,prompt_sha256=hashlib.sha256((read_prompt(prompt_name)+('\n'+WORLD_INSTRUCTION if world_structure else '')).encode()).hexdigest(),parameters=cfg.model_dump(mode='json'),wire_contract='source-world-v1' if world_structure else 'pdf-p0-extract-v3')
        fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        return value,private,documents,ruleset,identity,fingerprint
    async def start(self,draft,request):
        value,private,documents,ruleset,identity,fingerprint=self.inputs(draft,request)
        with self.preparation.connection() as db:
            old=db.execute('SELECT id,fingerprint FROM creation_runs WHERE draft=? AND request=?',(draft,request.request_id)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise ValueError('Идентификатор запроса уже использован для других материалов')
                return self.status(old['id'])
            running=db.execute('SELECT id FROM creation_runs WHERE draft=? AND status IN ("queued","running")',(draft,)).fetchone()
            if running:return self.status(running['id'])
            latest=db.execute('SELECT result FROM creation_runs WHERE draft=? ORDER BY created DESC LIMIT 1',(draft,)).fetchone()
            if latest and 'world_structure' not in request.model_fields_set:
                previous=json.loads(latest['result']) if latest['result'] else {}
                prior=resume_metadata(previous)['preparation_mode']
                # The hazard is a downgrade: a world run re-sent as a compact name list
                # would recharge the book and discard the structure. Omitting the flag is
                # not a downgrade now that an uploaded book defaults to world structure, so
                # it resumes the stored mode; an unknown legacy mode still needs a choice.
                if prior=='unknown':
                    raise ValueError('Явно выберите режим подготовки world_structure; режим продолжения не выбран. Источники повторно не отправлены.')
                if prior=='world_structure' and identity['wire_contract']!='source-world-v1':
                    raise ValueError('Сохранён структурный мир; компактный разбор не заменяет его автоматически. Выберите world_structure или новую подготовку. Источники повторно не отправлены.')
            metadata=PreparationResumeMetadata(preparation_mode=identity['wire_contract']=='source-world-v1' and 'world_structure' or 'source_extract',source_ruleset=ruleset).model_dump()
            book=book_fingerprint(identity)
            id=str(uuid.uuid4());db.execute('INSERT INTO creation_runs(id,draft,request,fingerprint,status,result,created,book) VALUES(?,?,?,?,"queued",?,?,?)',(id,draft,request.request_id,fingerprint,json.dumps(dict(preparation_metadata=metadata)),time.time(),book))
            # Тот же источник уже разобран целиком? Тогда блоки берутся из
            # creation_parts, а модель не вызывается ни разу.
            done=db.execute('SELECT id,done,total FROM creation_runs WHERE book=? AND book!="" AND id!=? AND done>0 AND done=total AND status IN ("ready","blocked") ORDER BY created DESC LIMIT 1',(book,id)).fetchone()
        if done:
            task=asyncio.create_task(self.reuse(id,value,private,documents,ruleset,identity,done['id']));self.tasks[id]=task
            task.add_done_callback(lambda finished:self.tasks.pop(id,None))
            return self.status(id)
        task=asyncio.create_task(self.run(id,value,private,documents,ruleset,identity));self.tasks[id]=task
        task.add_done_callback(lambda done:self.tasks.pop(id,None))
        return self.status(id)
    async def cancel(self,id):
        self.raw(id)
        with self.preparation.connection() as db:db.execute('UPDATE creation_runs SET status="cancelled",message="Подготовка отменена. Материалы и черновик сохранены." WHERE id=? AND status IN ("queued","running")',(id,))
        task=self.tasks.get(id)
        if task:task.cancel()
        return self.status(id)
    async def close(self):
        tasks=list(self.tasks.values())
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
    def source_batches(self,documents):
        # Read a bounded cursor, not the complete book into memory.
        batch=[];size=0;complete=True
        with self.library.db() as db:
            for document in sorted(documents):
                doc=db.execute('SELECT format,quality FROM documents WHERE id=?',(document,)).fetchone()
                if not doc:raise ValueError('Выбранный источник не найден')
                quality=json.loads(doc['quality'])
                # Blank/title/map pages do not prove missing *text*. Selected
                # image-only sources still fail the no-text guard below.
                #
                # A page counted as blank is left out of every batch below, so its
                # words can never be quoted. When no image sits on that page there
                # is nothing to explain the gap, and the book is incomplete: the
                # playable core may be on the page nobody can read. Pages that do
                # carry an image are deliberate maps or handouts, not losses.
                blanks=set(quality.get('blank_pages_list') or [])
                pictured=set(quality.get('pictured_pages') or [])
                if quality.get('partial_pages'):complete=False
                if blanks-pictured:complete=False
                for row in db.execute('SELECT number,label,content FROM pages WHERE document=? ORDER BY number',(document,)):
                    text=row['content']
                    if not text.strip():continue
                    # Segment long pages without dropping the remainder or its page reference.
                    for offset in range(0,len(text),8000):
                        part=dict(document=document,page=row['number'],printed_page=row['label'],offset=offset,text=text[offset:offset+8000])
                        if batch and size+len(part['text'])>16000:yield batch,complete;batch=[];size=0
                        batch.append(part);size+=len(part['text'])
        if batch:yield batch,complete
        elif not documents:yield [],True
    def original_document(self,bundle,text):
        from .adventure_worker import extract_document
        temporary=self.library.files/('.authored-'+uuid.uuid4().hex+'.part')
        try:
            temporary.write_text(text,encoding='utf8');sha=self.library.put_file(temporary)
            with self.library.db() as db:
                db.execute('INSERT OR IGNORE INTO documents(id,format,bytes) VALUES(?,".txt",?)',(sha,temporary.stat().st_size))
                db.execute('INSERT OR IGNORE INTO bundle_files VALUES(?,?,?,"other","authored")',(bundle,sha,'Авторский замысел.txt'))
            extract_document(self.library,sha,'.txt','other',None);return sha
        finally:
            if temporary.exists():temporary.unlink()
    def part(self,id):
        with self.preparation.connection() as db:return dict(db.execute('SELECT * FROM creation_parts WHERE id=?',(id,)).fetchone())
    def ensure_part(self,run,identity,pages,readable,ordinal):
        source=json.dumps(dict(pages=pages,readable=readable),ensure_ascii=False)
        fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        stable={**identity,'parameters':{k:v for k,v in identity.get('parameters',{}).items() if k not in ('price_checked_at','input_per_million','output_per_million','image_per_million')}}
        if 'parameters' in stable['parameters']:stable['parameters']['parameters']=sorted(stable['parameters']['parameters'])
        signature=hashlib.sha256(json.dumps([stable,pages,readable],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        id=hashlib.sha256((fingerprint+source).encode()).hexdigest()
        with self.preparation.connection() as db:
            cached=db.execute('SELECT id FROM creation_parts WHERE signature=? ORDER BY (status="validated") DESC,created LIMIT 1',(signature,)).fetchone()
            if cached:id=cached['id']
            db.execute('INSERT OR IGNORE INTO creation_parts(id,fingerprint,source,created) VALUES(?,?,?,?)',(id,fingerprint,source,time.time()))
            db.execute('UPDATE creation_parts SET signature=? WHERE id=? AND signature=""',(signature,id))
            db.execute('INSERT OR IGNORE INTO creation_part_links VALUES(?,?,?)',(run,id,ordinal))
        return id
    def part_update(self,id,**values):
        if not set(values)<={'status','body','raw','diagnostic','metric','children','attempts'}:raise ValueError('Недопустимое поле блока')
        with self.preparation.connection() as db:
            db.execute('UPDATE creation_parts SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),id))
            row=db.execute('SELECT * FROM creation_parts WHERE id=?',(id,)).fetchone()
            if row['attempts'] and values.get('status') in ('validated','failed','interrupted','split'):
                db.execute('INSERT OR IGNORE INTO creation_part_attempts VALUES(?,?,?,?,?,?,?)',(id,row['attempts'],row['status'],row['raw'],row['diagnostic'],row['metric'],time.time()))
    def leaves(self,roots):
        result=[]
        def walk(id):
            part=self.part(id);children=json.loads(part['children'])
            if children:
                for child in children:walk(child)
            else:result.append(part)
        for id in roots:walk(id)
        return result
    def refresh_progress(self,id,roots):
        leaves=self.leaves(roots);done=sum(p['status']=='validated' for p in leaves)
        self.update(id,done=done,total=len(leaves),message=f'Обработаны блоки: {done} / {len(leaves)}. Результаты сохранены.')
        return leaves
    async def reuse(self,id,value,private,documents,ruleset,identity,source):
        """Та же книга уже разобрана: собрать мир из сохранённых секций.

        Секции берутся из `creation_batch_history` прошлого прогона, поэтому
        модель не вызывается ни разу — ни одного оплаченного запроса. Мир при
        этом собирается для ТЕКУЩЕГО черновика: его описание, его подтверждение
        старта, его модуль, — а не копируется из чужого.
        """
        draft=value['id'];bundle=private['body']['adventure_bundle']
        try:
            with self.preparation.connection() as db:
                batches=[json.loads(r[0]) for r in db.execute('SELECT body FROM creation_batch_history WHERE run=? ORDER BY batch',(source,))]
                saved=db.execute('SELECT done,total FROM creation_runs WHERE id=?',(source,)).fetchone()
            if not batches:raise PreparationProblem('Сохранённый разбор книги пуст.')
            module='prepared-'+draft.replace('-','')[:24]
            original_document=self.original_document(bundle,value['description']) if not documents else ''
            result=PreparedWorldValidator(self.library,documents,original=not bool(documents),original_document=original_document).compile(
                batches,active_mechanics(),module=module,complete=True,source_ruleset=ruleset)
            if documents:
                from .first_playable import entry_result
                result=entry_result(self.library,documents,result,ruleset,module)
            result['start_contract']=dict(documents=sorted(documents),description_sha256=hashlib.sha256(value['description'].encode()).hexdigest(),world_structure=identity['wire_contract']=='source-world-v1')
            result['reused_from']=source
            self.apply_result(draft,bundle,ruleset,result)
            message='Эта книга уже разобрана, начинаем сразу. Секции взяты из сохранённого разбора — вызовов модели не было.'
            with self.preparation.connection() as db:
                # Строка прогона уже создана в start() со статусом queued: здесь
                # только её содержимое, иначе нарушится первичный ключ.
                db.execute('UPDATE creation_runs SET status=?,done=?,total=?,message=?,result=?,book=? WHERE id=?',
                    ('ready' if result['ready'] else 'blocked',saved['done'],saved['total'],message,json.dumps(result,ensure_ascii=False),
                     book_fingerprint(identity),id))
                db.execute('INSERT INTO creation_batch_history SELECT ?,batch,body FROM creation_batch_history WHERE run=?',(id,source))
                db.execute('INSERT INTO creation_part_links SELECT ?,part,ordinal FROM creation_part_links WHERE run=?',(id,source))
            self.status(id)
        except Exception as exc:
            # Молча начать платный разбор вместо готового нельзя: игрок заплатит
            # второй раз за ту же книгу, ничего об этом не зная.
            message=('Сохранённый разбор этой книги не читается ('+type(exc).__name__+'). Книга и её страницы могли исчезнуть из библиотеки. Подготовьте приключение заново явно.'
                if not isinstance(exc,PreparationProblem) else str(exc))
            self.update(id,status='failed',message=message)

    async def run(self,id,value,private,documents,ruleset,identity):
        draft=value['id'];bundle=private['body']['adventure_bundle']
        try:
            self.update(id,status='running',message='AI структурирует источники. Сценарий пока не готов.')
            batches=[];complete=True;index=0;roots=[]
            # Count a bounded metadata-only pass for a useful progress denominator.
            total=sum(1 for _ in self.source_batches(documents));self.update(id,total=total)
            if not total:raise PreparationProblem('Нет доступного текста источника. OCR/vision не выполнялись; замените скан текстовым PDF или опишите оригинальный замысел.')
            from .world import WorldLocation,WorldNPC,WorldRoute
            from .interactions import SceneObject
            from .adventure_contracts import ScenarioEvent,RollTable,Tracker
            from .inventory import CATALOG
            from .prompt_compaction import shared_contracts
            schemas=shared_contracts({model.__name__:model.model_json_schema() for model in (WorldLocation,WorldNPC,WorldRoute,SceneObject,ScenarioEvent,RollTable,Tracker)})
            for ordinal,(pages,readable) in enumerate(self.source_batches(documents)):
                roots.append(self.ensure_part(id,identity,pages,readable,ordinal*1000))
            self.refresh_progress(id,roots)
            from .preparation_format import decode,diagnostics,split_pages,source_input
            from .cloud import CloudUnavailable
            from .cloud_cost import BudgetExceeded
            stop_for_budget=False
            async def process(part_id,depth=0):
                nonlocal stop_for_budget
                part=self.part(part_id);source=json.loads(part['source']);pages=source['pages']
                children=json.loads(part['children'])
                if children:
                    for child in children:
                        with self.preparation.connection() as db:db.execute('INSERT OR IGNORE INTO creation_part_links VALUES(?,?,?)',(id,child,index*1000+depth))
                        if not stop_for_budget:await process(child,depth+1)
                    return
                if part['status']=='validated' or stop_for_budget:return
                if self.raw(id)['status']=='cancelled':raise asyncio.CancelledError()
                existing=[e['key'] for p in self.leaves(roots) if p['status']=='validated' for e in json.loads(p['body'])['entities']][-300:]
                rendered,spans,wire_schema=source_input(pages) if documents else (pages,{},PreparationBatch.model_json_schema())
                payload=dict(task='campaign-prepare-v1',description=value['description'],ruleset=ruleset,pages=rendered,batch=index,original=not bool(documents),contracts=schemas,item_catalog=[dict(id=k,name=v['name']) for k,v in CATALOG.items()],existing_keys=existing,response_budget=dict(max_entities=3 if documents else 8,max_quote_characters=240,compact=True))
                if documents and identity['wire_contract']!='source-world-v1':
                    from .source_extraction import request as source_request
                    payload,spans,wire_schema=source_request(pages,value['description'],ruleset,index,existing)
                if part['status']=='failed' and part['raw']:
                    try:
                        # Repair must read the wire format this block was generated in. A
                        # world block is a PreparationBatch; only the legacy compact run
                        # emits the names-only payload.
                        if documents and identity['wire_contract']!='source-world-v1':
                            from .source_extraction import parse as parse_source
                            saved,repairs=parse_source(part['raw'],spans,ruleset)
                        else:saved,repairs=decode(part['raw'],spans)
                    except ValueError:pass
                    else:
                        self.part_update(part_id,status='validated',body=saved.model_dump_json(),diagnostic=json.dumps([dict(field='$',reason='local_repair',message=r) for r in repairs]))
                        self.refresh_progress(id,roots);return
                self.part_update(part_id,status='running',attempts=part['attempts']+1)
                chunks=[];length=0;metric_ids={object_id(m) for m in self.llm.metrics};metric={}
                try:
                    with self.llm.scope('preparation:'+draft,id+':'+part_id[:16]):
                        world_instruction=WORLD_INSTRUCTION if identity['wire_contract']=='source-world-v1' else ''
                        async with aclosing(self.llm.stream(world_instruction,json.dumps(payload,ensure_ascii=False),mode='preparation_world',grammar=wire_schema,source_extract=bool(documents) and identity['wire_contract']!='source-world-v1')) as stream:
                            async for chunk in stream:
                                length+=len(chunk)
                                if length>512000:raise PreparationProblem('Ответ блока превышает безопасный размер')
                                chunks.append(chunk)
                    if self.llm.metrics and object_id(self.llm.metrics[-1]) not in metric_ids:metric=self.llm.metrics[-1].copy()
                    if documents:
                        from .source_extraction import parse as parse_source
                        result,repairs=parse_source(''.join(chunks),spans,ruleset)
                    else:result,repairs=decode(''.join(chunks),spans)
                    self.part_update(part_id,status='validated',body=result.model_dump_json(),raw=''.join(chunks),diagnostic=json.dumps([dict(field='$',reason='format_normalization',message=r) for r in repairs]),metric=json.dumps(metric))
                except asyncio.CancelledError:
                    self.part_update(part_id,status='interrupted',raw=''.join(chunks));raise
                except Exception as exc:
                    if self.llm.metrics and object_id(self.llm.metrics[-1]) not in metric_ids:metric=self.llm.metrics[-1].copy()
                    issue=diagnostics(exc)
                    if isinstance(exc,(CloudUnavailable,BudgetExceeded)) or not metric:
                        issue=[dict(field='$',reason='provider_or_budget',message=str(exc)[:300] if isinstance(exc,(CloudUnavailable,BudgetExceeded)) else 'Вызов блока не выполнен')]
                    self.part_update(part_id,status='failed',raw=''.join(chunks),diagnostic=json.dumps(issue,ensure_ascii=False),metric=json.dumps(metric))
                    if metric and hasattr(self.llm,'contract_error') and not isinstance(exc,(CloudUnavailable,BudgetExceeded)):self.llm.contract_error('invalid_campaign_preparation')
                    children_pages=split_pages(pages) if metric.get('hit_token_limit') and depth<3 else []
                    if children_pages:
                        child_ids=[self.ensure_part(id,identity,child,source['readable'],index*1000+depth*10+n) for n,child in enumerate(children_pages)]
                        self.part_update(part_id,status='split',children=json.dumps(child_ids))
                        for child in child_ids:
                            if not stop_for_budget:await process(child,depth+1)
                    elif isinstance(exc,BudgetExceeded) or 'budget' in str(exc).casefold() or 'общий подтверждённый лимит' in str(exc).casefold():stop_for_budget=True
                self.refresh_progress(id,roots)
                await asyncio.sleep(0)
            for index,root in enumerate(roots):
                if not stop_for_budget:await process(root)
            leaves=self.refresh_progress(id,roots)
            for index,part in enumerate(leaves):
                if part['status']!='validated':continue
                result=PreparationBatch.model_validate_json(part['body'])
                with self.preparation.connection() as db:db.execute('INSERT OR REPLACE INTO creation_batch_history VALUES(?,?,?)',(id,index,result.model_dump_json()))
                batches.append(result.model_dump());complete=complete and json.loads(part['source'])['readable']
            if len(batches)!=len(leaves):
                issue=next((json.loads(p['diagnostic'])[0] for p in leaves if p['status']=='failed' and json.loads(p['diagnostic'])),None)
                detail=(' '+issue['field']+': '+issue['message']) if issue else ''
                self.update(id,status='partial' if batches else 'failed',message=f'Сохранено блоков: {len(batches)} / {len(leaves)}. Продолжить — обработать только незавершённые блоки.'+detail);return
            if sum(len(json.dumps(b,ensure_ascii=False)) for b in batches)>4000000:raise PreparationProblem('Подготовленный материал слишком велик. Проверенные блоки сохранены.')
            original_document=self.original_document(bundle,value['description']) if not documents else ''
            result=PreparedWorldValidator(self.library,documents,original=not bool(documents),original_document=original_document).compile(batches,active_mechanics(),module='prepared-'+draft.replace('-','')[:24],complete=complete,source_ruleset=ruleset)
            if documents:
                from .first_playable import entry_result
                result=entry_result(self.library,documents,result,ruleset,'prepared-'+draft.replace('-','')[:24])
            result['start_contract']=dict(documents=sorted(documents),description_sha256=hashlib.sha256(value['description'].encode()).hexdigest(),world_structure=identity['wire_contract']=='source-world-v1')
            # Reject stale results if the player changed text or selected files during inference.
            latest=self.creation.get(draft)
            if latest['description']!=value['description'] or self.creation.allowed_documents(draft)!=documents:raise PreparationProblem('Материалы или описание изменились. Подготовьте новую версию; старый ответ не применён.')
            self.apply_result(draft,bundle,ruleset,result)
            message=(result.get('entry_readiness') or {}).get('reason') or ('Приключение подготовлено. Создайте героя.' if result['ready'] else 'Часть сценария не прошла проверку. Неподдержанные важные элементы изолированы; запуск заблокирован.')
            self.update(id,status='ready' if result['ready'] else 'blocked',result=json.dumps(result,ensure_ascii=False),message=message)
        except asyncio.CancelledError:
            self.update(id,status='interrupted',message='Подготовка прервана. Черновик и исходники сохранены.')
            raise
        except Exception as exc:
            # Contract diagnostics are private; avoid quoting book secrets in the player error.
            from .llm import CloudUnavailable,BudgetExceeded
            message=str(exc) if isinstance(exc,(CloudUnavailable,BudgetExceeded,PreparationProblem)) else 'Подготовка не завершена. Исходники сохранены; проверьте подключение, лимит расходов или повторите попытку.'
            self.update(id,status='failed',message=message)
