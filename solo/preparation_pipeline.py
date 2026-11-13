"""On-demand, cancellable Qwen preparation with persisted progress and private results."""
import asyncio,hashlib,json,logging,os,threading,time,uuid
from builtins import id as object_id
from contextlib import aclosing
from .preparation_contracts import PrepareRequest,PreparationBatch,PreparationResumeMetadata
from .prepared_world import PreparedWorldValidator
from .preparation import Draft
from .preparation_usage import run_usage

logger=logging.getLogger('solo.preparation')

class PreparationProblem(ValueError):pass


def entry_refusal(run):
    """Почему входа ещё нет — словами игрока, а не путём по базе.

    «Подготовка не завершена: 96 / 96. Продолжите сохранённые блоки» показывалось
    и тогда, когда продолжать было нечего: все блоки разобраны, но сборка мира
    не сошлась. Игрок не понимает ни слова, а кнопка, которая это лечит, стоит
    в полосе подготовки под своим именем — его и называем.
    """
    done,total=int(run['done']),int(run['total'])
    if total and done==total:
        return (f'Все {total} частей книги разобраны, но глава из них ещё не собралась. '
                'Нажмите «Собрать мир из готовых блоков» — повторно платить не нужно.')
    return (f'Глава ещё собирается: {done} из {total} частей. '
            'Нажмите «Продолжить незавершённые блоки».')

#: Сколько попыток даётся одному куску книги до честного `failed`.
#: Первая — полная задача; вторая и третья идут с урезанным заданием
#: (`response_budget.max_entities`), потому что длинный ответ ломается чаще.
#: После третьей отказ окончателен: остальные блоки всё равно едут дальше.
MAX_ATTEMPTS=3
# Отказ провайдера (503 «зеркала модели») — отдельный, более терпеливый счёт:
# ждём 2, 5 и 10 секунд, прежде чем признать блок безнадёжным.
PROVIDER_ATTEMPTS=3
PROVIDER_BACKOFF=(2,5,10)
#: Сколько ПЕРВЫХ блоков книги должно быть принято, чтобы открыть главу 1 и
#: дать играть, не дожидаясь остальных. Один блок — это уже начало книги;
#: второй нужен, когда первый не назвал маршрута и входа не получилось.
PLAYABLE_ROOTS=2
#: Прогон в `running` без живого воркера дольше этого считается брошенным.
STALE_SECONDS=90.0
#: Статусы, после которых воркера у прогона нет и быть не должно.
TERMINAL=('ready','blocked','partial','failed','cancelled','interrupted')


def current_build():
    """Отпечаток кода, который сейчас исполняется.

    Нужен, чтобы отличить «мир не собрался этим кодом» от «мир не собрался
    прежним»: во втором случае после правки движка сборку стоит повторить —
    она локальная и денег не стоит. Берём коммит (стабильный между
    перезапусками одного и того же кода); дайджест `build` зависит от того,
    какие модули успели загрузиться к первому вызову, и на нём прогон
    пересобирался бы при каждом старте.
    """
    try:
        from .build_identity import build_identity
        identity=build_identity()
        commit=identity.get('commit') or ''
        return commit if commit and commit!='unknown' else (identity.get('build') or '')
    except Exception:
        return ''


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


def resume_metadata(result):
    """Whitelist only explicit persisted mode and edition; never infer from progress."""
    result=result if isinstance(result,dict) else {}
    metadata=result.get('preparation_metadata')
    metadata=metadata if isinstance(metadata,dict) else {}
    contract=result.get('start_contract')
    flag=contract.get('world_structure') if isinstance(contract,dict) else None
    # r240: пустая запись режима не должна прятать контракт, который режим
    # называет. Сборка из готовых блоков пишет `start_contract.world_structure`,
    # а `preparation_metadata` может прийти без режима — и тогда готовый мир
    # нельзя было подтвердить: «режим неизвестен», игра не начинается.
    mode=metadata.get('preparation_mode')
    if mode not in ('world_structure','source_extract'):
        mode='world_structure' if flag is True else 'source_extract' if flag is False else 'unknown'
    source=metadata.get('source_ruleset')
    if source not in ('dnd2014','dnd2024'):
        editions={d.get('ruleset') for d in (result.get('definition'),result.get('full_definition'))
                  if isinstance(d,dict) and d.get('ruleset') in ('dnd2014','dnd2024')}
        source=next(iter(editions)) if len(editions)==1 else None
    return PreparationResumeMetadata(
        preparation_mode=mode,source_ruleset=source).model_dump()

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

#: Один ремонт на блок: переписать целиком, исправив только перечисленное.
#: Ошибки приходят строками «путь: причина», прошлый ответ — рядом, чтобы
#: модель копировала исправные элементы дословно и не выдумывала новых.
REPAIR_INSTRUCTION=('Предыдущий ответ не прошёл проверку структуры. Перепиши ЕГО ЖЕ целиком по той же схеме: '
    'исправь только перечисленные ошибки, остальные элементы скопируй дословно. '
    'Не добавляй новые факты, сущности, связи или ключи, которых нет в схеме. '
    'Не объясняй и не извиняйся. Ответ — только JSON-объект.')

def repair_input(raw,exc):
    """Вход ремонтного запроса: ошибки pydantic построчно и прошлый ответ."""
    from .structured_output import error_lines
    return json.dumps(dict(task='repair-preparation-block-v1',errors=error_lines(exc),
        previous_answer=str(raw)[:40000]),ensure_ascii=False)

#: Коды НАШИХ локальных потолков ответа. Поймали такой — секция дробится и
#: повторяется; игроку отказ показывают только когда дробить уже нечего.
#: `stream_deadline` здесь потому, что и он значит «ответ не влез»: модель
#: ещё пишет, а бюджет времени на одну секцию уже кончился.
LIMIT_CODES=('stream_byte_limit','response_byte_limit','stream_line_limit','stream_event_limit','stream_deadline')

def page_span(pages):
    """«страницы 12–13» для честной ошибки: какой фрагмент книги не влез."""
    numbers=sorted({p.get('page') for p in pages if isinstance(p.get('page'),int)})
    if not numbers:return 'фрагмент без номера страницы'
    if len(numbers)==1:return 'страница '+str(numbers[0])
    return 'страницы '+str(numbers[0])+'–'+str(numbers[-1])

#: Код диагностики — не текст для игрока. Здесь закрытый словарь причин: он
#: рождается в `preparation_format.diagnostics` и в обработчике отказа блока,
#: а не в модели. Незнакомый ASCII-код считается техническим (это либо тип
#: pydantic, либо наш новый код) и на экран не попадает.
REFUSAL_TEXT={'unknown_source_span':'цитата не подтверждена страницей',
    'json_invalid':'ответ модели не разобрался как ответ',
    'section_structure_error':'структура блока не сошлась',
    'source_section_too_large':'блок оказался слишком большим для одного ответа',
    'section_split':'блок оказался слишком большим для одного ответа',
    'provider_or_budget':'модель не ответила',
    'local_repair':'ответ пришлось править локально',
    'format_normalization':'ответ пришлось привести к формату',
    'schema_dropped_key':'ответ принёс лишние поля'}

def one_line(value,limit=140):
    """Одна строка без переносов и хвоста: список причин не должен «съезжать»."""
    text=' '.join(str(value or '').split())
    return text if len(text)<=limit else text[:limit-1].rstrip()+'…'

def refusal_text(diagnostic,status='failed'):
    """Короткая причина отказа блока — словами, без полей схемы и кодов.

    Владельцу нужно решить «ждать или добивать», а не читать `string_type`:
    поэтому технический код остаётся в `reason`, а показывается фраза.
    """
    diagnostic=diagnostic if isinstance(diagnostic,dict) else {}
    reason=str(diagnostic.get('reason') or '')
    message=one_line(diagnostic.get('message'))
    text=REFUSAL_TEXT.get(reason)
    if text is None:
        # Русское сообщение можно показать как есть: это наша фраза, а не
        # английский ответ провайдера и не имя типа pydantic.
        cyrillic=any('а'<=ch.lower()<='я' or ch=='ё' for ch in message)
        if message and cyrillic:text=message
        elif reason and reason.isascii():text='ответ модели не по схеме'
        else:text=message or 'ответ не прошёл проверку'
    if status=='interrupted' and not diagnostic:return'разбор прервался, блок не дочитан'
    return one_line(text)

def section_refusal(pages,metric,minimum,split=False):
    """Причина отказа секции: размер, потолок роли и что с этим делать.

    Без этого сообщения «провайдер недоступен» скрывало, что дело не в сети и
    не в деньгах, а в конкретном куске книги.
    """
    characters=sum(len(p.get('text') or '') for p in pages)
    limit=metric.get('byte_limit') or (metric.get('stream_limits') or {}).get('bytes')
    cap=(' при потолке ответа '+str(limit)+' байт') if limit else ''
    tokens=metric.get('output_limit_tokens')
    if tokens:cap+=' (max_tokens='+str(tokens)+')'
    if split:
        return 'Секция ('+page_span(pages)+', '+str(characters)+' символов) не уложилась в ответ'+cap+'. Делим пополам и повторяем.'
    if characters<=minimum:
        return ('Фрагмент книги ('+page_span(pages)+', '+str(characters)+' символов) уже меньше минимальной части ('
            +str(minimum)+' символов) и не уложился в ответ'+cap+'. Разделите книгу по главам или поднимите max_tokens роли подготовки осознанно.')
    # Секция ещё не «минимальная», но деление кончилось: предел глубины.
    # Говорим это прямо, а не выдаём её за крошечный фрагмент.
    return ('Фрагмент книги ('+page_span(pages)+', '+str(characters)+' символов) не уложился в ответ'+cap
        +' даже после деления. Разделите книгу по главам или поднимите max_tokens роли подготовки осознанно.')

class PreparationPipeline:
    def __init__(self,creation,llm,store):
        self.creation=creation;self.library=creation.library;self.preparation=creation.preparation;self.llm=llm;self.store=store;self.tasks={}
        # Живой прогресс прогона: время старта, фаза и какая секция читается
        # сейчас. Держится в памяти процесса — в базу уходит только снимок.
        self.concurrency=1;self.started={};self.phases={};self.cursor={}
        # r237: глава 1 открывает игру раньше всей книги. `prime` — «вход уже
        # открыт», `prime_lock` — чтобы два закончивших одновременно блока не
        # собрали и не применили вход дважды.
        self.prime={};self.prime_lock=threading.Lock()
        # Кто именно сейчас разбирает прогон. Токен процесса-владельца пишется
        # в базу рядом с сердцебиением: после рестарта по нему видно, что
        # воркера нет, а прогон остался, и его надо поднять, а не ждать кнопки.
        self.worker=f'{os.getpid()}-{uuid.uuid4().hex[:8]}'
        # Где лежит леджер расходов. У живого приложения это путь `llm.ledger`,
        # у тестов и офлайн-сборки — рядом с базами профиля. Пути нет — счётчик
        # просто молчит (`run_usage` возвращает `None`), разбор книги не падает.
        self.ledger_path=getattr(getattr(llm,'ledger',None),'path',None) or self.preparation.path.parent/'cloud-usage.sqlite3'
        self.watchdog=None
        self.preparation.preparation_pipeline=self
        with self.preparation.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS creation_runs(id TEXT PRIMARY KEY,draft TEXT NOT NULL REFERENCES drafts(id),request TEXT NOT NULL,fingerprint TEXT NOT NULL,status TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,message TEXT NOT NULL DEFAULT "",result TEXT,created REAL NOT NULL,UNIQUE(draft,request))')
            db.execute('CREATE TABLE IF NOT EXISTS creation_batch_history(run TEXT NOT NULL REFERENCES creation_runs(id),batch INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(run,batch))')
            db.execute('CREATE TABLE IF NOT EXISTS creation_parts(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,source TEXT NOT NULL,status TEXT NOT NULL DEFAULT "queued",body TEXT,raw TEXT,diagnostic TEXT NOT NULL DEFAULT "[]",metric TEXT NOT NULL DEFAULT "{}",children TEXT NOT NULL DEFAULT "[]",attempts INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL)')
            if 'signature' not in {r['name'] for r in db.execute('PRAGMA table_info(creation_parts)')}:db.execute('ALTER TABLE creation_parts ADD COLUMN signature TEXT NOT NULL DEFAULT ""')
            # Живой прогресс — отдельно от `message`: по нему рисуется полоса
            # ожидания, а `message` остаётся человеческой фразой для лога.
            if 'progress' not in {r['name'] for r in db.execute('PRAGMA table_info(creation_runs)')}:db.execute('ALTER TABLE creation_runs ADD COLUMN progress TEXT NOT NULL DEFAULT "{}"')
            db.execute('CREATE INDEX IF NOT EXISTS creation_parts_signature ON creation_parts(signature)')
            # r236: отпечаток ИСХОДНОЙ КНИГИ — без черновика и описания. По нему
            # повторная загрузка той же книги поднимает готовые секции, а не
            # покупает разбор заново.
            columns={r['name'] for r in db.execute('PRAGMA table_info(creation_runs)')}
            if 'book_fingerprint' not in columns:db.execute('ALTER TABLE creation_runs ADD COLUMN book_fingerprint TEXT NOT NULL DEFAULT ""')
            if 'reused' not in columns:db.execute('ALTER TABLE creation_runs ADD COLUMN reused INTEGER NOT NULL DEFAULT 0')
            # r237: «первая глава готова — играть можно, остальное готовится».
            # Флаг ставится один раз и переживает перезапуск процесса.
            if 'playable' not in columns:db.execute('ALTER TABLE creation_runs ADD COLUMN playable INTEGER NOT NULL DEFAULT 0')
            db.execute('CREATE INDEX IF NOT EXISTS creation_runs_book ON creation_runs(book_fingerprint)')
            db.execute('CREATE TABLE IF NOT EXISTS creation_part_links(run TEXT NOT NULL REFERENCES creation_runs(id),part TEXT NOT NULL REFERENCES creation_parts(id),ordinal INTEGER NOT NULL,PRIMARY KEY(run,part))')
            db.execute('CREATE TABLE IF NOT EXISTS creation_part_attempts(part TEXT NOT NULL REFERENCES creation_parts(id),attempt INTEGER NOT NULL,status TEXT NOT NULL,raw TEXT,diagnostic TEXT NOT NULL,metric TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(part,attempt))')
            db.execute('INSERT OR IGNORE INTO creation_part_attempts SELECT id,attempts,status,raw,diagnostic,metric,created FROM creation_parts WHERE attempts>0 AND status IN ("validated","failed","interrupted","split")')
            # r238: прогон живёт в базе, а не во вкладке. `worker` — кто его
            # сейчас разбирает (пустая строка = никто), `heartbeat` — когда этот
            # воркер последний раз подавал голос, `resume` — всё, что нужно,
            # чтобы продолжить с того же куска после рестарта и попасть в
            # кэш уже оплаченных секций.
            run_columns={r['name'] for r in db.execute('PRAGMA table_info(creation_runs)')}
            if 'worker' not in run_columns:db.execute('ALTER TABLE creation_runs ADD COLUMN worker TEXT NOT NULL DEFAULT ""')
            if 'heartbeat' not in run_columns:db.execute('ALTER TABLE creation_runs ADD COLUMN heartbeat REAL NOT NULL DEFAULT 0')
            if 'resume' not in run_columns:db.execute('ALTER TABLE creation_runs ADD COLUMN resume TEXT NOT NULL DEFAULT ""')
            db.execute('UPDATE creation_parts SET status="interrupted" WHERE status="running"')
            # Прогон, который шёл в прошлом процессе, воркера больше не имеет:
            # `running` без воркера — это ложь на экране. Статус становится
            # `interrupted`, и его тут же поднимает `resume_pending` на старте.
            db.execute('UPDATE creation_runs SET status="interrupted",worker="",message="Подготовка прервана перезапуском. Продолжу сам с того же блока." WHERE status IN ("queued","running")')
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
        # Метрики структуры: сколько ответов пришло схемой, сколько ключей модель
        # придумала и сколько было ремонтов. Только счётчики, без имён из книги.
        # Журнал секций: сколько их, сколько байт занял каждый ответ и какой
        # запас остался до потолка роли. Без этих чисел снова пришлось бы гадать.
        structure=dict(calls=0,modes=Counter(),dropped_keys=0,repairs=0,duration_ms=0.0,
            sections=int(row['total'] or 0),answered_sections=0,response_bytes=0,max_response_bytes=0,
            min_headroom=None,limit_hits=0,split_sections=0,output_limit_tokens=None,byte_limit=None,responses=[])
        with self.preparation.connection() as db:
            for part_row in db.execute('SELECT p.metric,p.status,l.ordinal FROM creation_parts p JOIN creation_part_links l ON l.part=p.id WHERE l.run=?',(id,)):
                try:metric=json.loads(part_row['metric'] or '{}')
                except ValueError:continue
                if not isinstance(metric,dict):continue
                if not metric.get('calls') and not metric.get('bytes'):continue
                # Занято потолка считаем по всему потоку (включая reasoning и SSE),
                # а не по видимому тексту: обрыв случился бы именно по нему.
                size=max(int(metric.get('bytes') or 0),int(metric.get('stream_bytes') or 0));cap=metric.get('byte_limit') or (metric.get('stream_limits') or {}).get('bytes')
                entry=dict(section=int(part_row['ordinal'])//1000+1,status=part_row['status'],bytes=size,calls=int(metric.get('calls') or 0),
                    tokens=int(metric.get('output_tokens') or 0),
                    input_tokens=int(metric.get('actual_input_tokens') or metric.get('planned_input_tokens') or 0),
                    max_tokens=metric.get('max_tokens'),duration_ms=round(float(metric.get('duration_ms') or 0),2),
                    split_depth=int(metric.get('split_depth') or 0),limit_code=metric.get('limit_code'))
                if cap:
                    headroom=round(1-size/float(cap),4);entry['byte_limit']=int(cap)
                    structure['byte_limit']=int(cap)
                    # Запас считаем по ПРИНЯТЫМ ответам: ответ, который упёрся в
                    # потолок, специально разделён, и его близость к стене — не
                    # дефект, а причина деления. Приёмка «запас ≥30 %» относится
                    # ровно к тому, что вошло в мир.
                    if part_row['status']=='validated':
                        entry['headroom']=headroom
                        structure['min_headroom']=headroom if structure['min_headroom'] is None else min(structure['min_headroom'],headroom)
                if metric.get('limit_code'):structure['limit_hits']+=1
                if part_row['status']=='split':
                    # Вызовы, потраченные на секцию до деления, тоже оплачены:
                    # без них журнал занижает цену деления.
                    structure['split_sections']+=1;entry['split']=True
                else:
                    structure['answered_sections']+=1
                structure['response_bytes']+=size
                structure['max_response_bytes']=max(structure['max_response_bytes'],size)
                structure['output_limit_tokens']=max(structure['output_limit_tokens'] or 0,int(metric.get('max_tokens') or 0)) or structure['output_limit_tokens']
                structure['responses'].append(entry)
                structure['calls']+=int(metric.get('calls') or 0)
                # Режим каждого вызова, а не последнего: откат на json_object
                # внутри блока иначе не виден в сводке прогона.
                for mode in metric.get('schema_modes') or ([metric['schema_mode']] if metric.get('schema_mode') else []):
                    structure['modes'][mode]+=1
                structure['dropped_keys']+=len(metric.get('schema_dropped_keys') or [])
                structure['repairs']+=int(metric.get('repairs') or 0)
                structure['duration_ms']+=float(metric.get('duration_ms') or 0)
        structure['modes']=dict(structure['modes']);structure['duration_ms']=round(structure['duration_ms'],2)
        structure['responses']=sorted(structure['responses'],key=lambda r:(r['section'],r['bytes']))
        structure['responses_total']=len(structure['responses']);structure['responses']=structure['responses'][:200]
        # Expose causes, never rejected source values or raw Pydantic inputs.
        safe_reasons=('Цитата не подтверждена страницей','Нет происхождения факта','Неизвестная локация NPC','Механический обработчик этого элемента не зарегистрирован','Повтор имени локации','Текстовое сведение не подтверждено цитатой (поле name)','Стартовая локация не подтверждена','Есть страницы без доступного текста')
        canonical_issues=[dict(kind=k,count=n,reason=reason if reason in safe_reasons else 'Элемент не прошёл проверку канона; подробности сохранены отдельно') for (k,reason),n in issues.items()][:12]
        # r237: вход уже открыт, а книга ещё читается. Числа берём из базы, а не
        # из головы: «готово 6 из 30» — это done/total прогона.
        playable=bool(row.get('playable'))
        message=row['message']
        if playable and row['status'] in ('queued','running'):
            message=f'Можно играть: готово {row["done"]} из {row["total"]}, остальное готовится.'
        return dict(id=id,draft=row['draft'],status=row['status'],done=row['done'],total=row['total'],message=message,reused=bool(row.get('reused')),playable=playable,progress=json.loads(row['progress'] or '{}'),ready=bool(row['status']=='ready' and metadata['preparation_mode']!='unknown'),errors=errors,canonical_issues=canonical_issues,structure=structure,text_only=True,retry=self.retry_view(id),usage=self.usage(id,row),entry_readiness=result.get('entry_readiness') if result else None,summary=dict(locations=len(world.get('locations',[])),npc=sum(len(p['npc']) for p in world.get('locations',[])),quarantined=len(result.get('quarantine',[]))) if definition else None,**metadata)

    def retry_view(self,id):
        """Что осталось и что с этим делать — для честного итога и кнопки.

        `blocks` — не просто число: у каждого блока есть страницы и причина,
        иначе «3 блока не разобрались» ничем не отличается от молчания.
        """
        left=self.remaining(id)
        row=self.raw(id)
        # r240: все блоки разобраны, а мира нет — остатка нет, но работа есть:
        # сборка из готовых секций. Модель для неё не нужна.
        assembly=bool(left['total'] and left['done']==left['total']
            and not left['resumable'] and row['status']!='ready')
        return dict(done=left['done'],total=left['total'],pending=left['pending'],
            failed=left['failed'],exhausted=left['exhausted'],
            # Кнопка «добить» есть и тогда, когда попытки исчерпаны: это
            # осознанный повтор отказавших блоков, а не тихая переоплата.
            can_retry=bool(left['resumable'] or left['details'] or assembly),
            assembly=assembly,blocks=left['details'])

    def usage(self,id,row=None):
        """Сколько стоил прогон: вызовы, токены и отказы — из леджера расходов.

        Связь — по `calls.turn_ref` (`<id прогона>:<часть>`), схема леджера не
        меняется. Нет записей или самого леджера — `None`: старый прогон не
        обязан иметь счётчик, и экран обязан это пережить.
        """
        row=row or self.raw(id)
        return run_usage(self.ledger_path,id,blocks=int(row['done'] or 0),total=int(row['total'] or 0))

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

    def latest_run(self,draft):
        """Свежий прогон черновика — то, по чему экран считает главу.

        Числа берутся из базы, а не из памяти процесса: `world-readiness`
        отвечает и из другого процесса, и после перезапуска.
        """
        with self.preparation.connection() as db:
            row=db.execute('SELECT id,status,done,total,playable FROM creation_runs WHERE draft=? ORDER BY created DESC LIMIT 1',(draft,)).fetchone()
        if row is None:raise ValueError('Нет сохранённой подготовки мира.')
        return dict(row)

    def world_result(self,draft,run_id=None):
        """Existing validated full world; pinned observation entries never promote it."""
        current=self.creation.owned(draft)
        if run_id is None:run_id=self.latest_run(draft)['id']
        run=self.raw(run_id)
        if run['draft']!=draft:raise ValueError('Подготовка принадлежит другому черновику.')
        # r237: прогон ещё читает книгу, но глава 1 уже собрана — вход открыт
        # и играть можно. Мир при этом помечен `playable`, а не «полностью готов».
        playable=bool(run.get('playable'))
        # r245: «Можно играть» — это и есть разрешение войти, а не обещание на
        # будущее. Отмена и оборванный разбор оставляли игрока с открытой главой
        # и запертой кнопкой «Начать»: сообщение говорит «играйте», а проверка
        # требовала идущего прогона. Ниже мир всё равно проверяется по
        # контракту, документам и открытой стартовой сцене, так что вход не
        # становится шире, чем подтверждённый.
        if not playable and (run['status'] not in ('ready','blocked') or run['done']!=run['total']):
            raise ValueError(entry_refusal(run))
        if not run['result']:raise ValueError('Глава ещё не собрана: ни одна часть не принята. Запустите подготовку заново.')
        result=json.loads(run['result']);contract=result.get('start_contract')
        # r250: уже собранный мир мог остаться без описания стартового места —
        # тогда первый ход мастера нечем подтвердить. Текст берётся из того же
        # проверенного абзаца места и без вызовов модели; отпечаток сцены
        # пересчитывается здесь же, чтобы повторная проверка входа сошлась.
        # Страница названа в `entry_stage` (документ + номер) — её и читаем.
        from .first_playable import entry_page_text,publish_entry_scene
        result=publish_entry_scene(result,page_text=entry_page_text(self.library,result,current['body'].get('adventure_bundle')))
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
            db.execute('INSERT INTO creation_runs(id,draft,request,fingerprint,status,done,total,message,result,created,book_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(id,draft,'local-'+id,fingerprint,'ready' if result['ready'] else 'blocked',run['done'],run['total'],message,json.dumps(result,ensure_ascii=False),time.time(),run['book_fingerprint'] if 'book_fingerprint' in run.keys() else ''))
            db.execute('INSERT INTO creation_batch_history SELECT ?,batch,body FROM creation_batch_history WHERE run=?',(id,run['id']))
            db.execute('INSERT INTO creation_part_links SELECT ?,part,ordinal FROM creation_part_links WHERE run=?',(id,run['id']))
        return self.status(id)
    def latest(self,draft):
        with self.preparation.connection() as db:row=db.execute('SELECT id FROM creation_runs WHERE draft=? ORDER BY created DESC LIMIT 1',(draft,)).fetchone()
        value=self.status(row[0]) if row else None
        if value and value['status']=='ready' and not self.creation.owned(draft)['body']['prepared']:
            value.update(status='stale',ready=False,message='Описание или материалы изменились. Подготовьте приключение заново.')
        return value
    def heartbeat(self,id):
        """«Я жив» от воркера: без этого сторож не отличит работу от тишины."""
        with self.preparation.connection() as db:db.execute(
            'UPDATE creation_runs SET heartbeat=?,worker=? WHERE id=? AND status IN ("queued","running")',(time.time(),self.worker,id))
    async def keepalive(self,id,seconds=20.0):
        """Сердцебиение, пока идёт вызов модели: один кусок бывает и на минуту."""
        while True:
            self.heartbeat(id)
            await asyncio.sleep(seconds)
    def remaining(self,id):
        """Что в прогоне ещё не сделано — по базе, а не по памяти процесса.

        `pending` — куски, которые имеет смысл доделывать (`queued`,
        `interrupted`), `failed` — уже отказавшие, но ещё не исчерпавшие три
        попытки, `exhausted` — отказ окончательный. Сторож и кнопка «добить»
        смотрят сюда и не гадают.
        """
        with self.preparation.connection() as db:
            rows=list(db.execute('SELECT p.status,p.attempts,p.source,p.diagnostic,p.children,l.ordinal FROM creation_parts p '
                'JOIN creation_part_links l ON l.part=p.id WHERE l.run=?',(id,)))
        leaves=[];broken={}
        for row in rows:
            status=row['status'];attempts=int(row['attempts'] or 0)
            children=str(row['children'] or '')
            # r240: родитель, разделённый на детей, работу не делает — её делают
            # дети, и они же в этом счёте. Пока прерванный родитель попадал сюда
            # как `pending`, прогон с полностью принятыми детьми считался
            # незаконченным и уходил в «частично» мимо сборки мира.
            if status=='validated':kind='done'
            elif children not in ('','[]'):kind='split'
            elif attempts>=MAX_ATTEMPTS and status in ('failed','interrupted'):kind='exhausted'
            elif status=='failed':kind='failed'
            elif status=='split':kind='split'
            else:kind='pending'
            leaves.append(dict(status=status,attempts=attempts,kind=kind,ordinal=int(row['ordinal'])))
            if kind not in ('failed','exhausted'):continue
            # Честный итог называет блок, страницы и причину: «3 блока не
            # разобрались» без списка — это отписка, а не ответ владельцу.
            block=int(row['ordinal'])//1000+1
            if block in broken:continue
            try:pages=json.loads(row['source']).get('pages') or []
            except ValueError:pages=[]
            try:diagnostic=(json.loads(row['diagnostic'] or '[]') or [{}])[0]
            except (ValueError,IndexError):diagnostic={}
            text=str(diagnostic.get('message') or '')
            broken[block]=dict(block=block,pages=page_span(pages),status=status,reason=diagnostic.get('reason'),
                # r241: короткая причина одной строкой. Экран показывает её, а не
                # код диагностики: «ответ модели не по схеме» решает, ждать или
                # добивать, а `string_type` — нет.
                short=refusal_text(diagnostic,status),
                message=(text[:200]+'…' if len(text)>200 else text) or None,
                attempts=attempts,exhausted=kind=='exhausted')
        count=lambda kind:sum(1 for leaf in leaves if leaf['kind']==kind)
        total=count('done')+count('pending')+count('failed')+count('exhausted')
        return dict(done=count('done'),pending=count('pending'),failed=count('failed'),
            exhausted=count('exhausted'),total=total,
            # План ещё не посчитан (процесс умер сразу после старта) — это тоже
            # остаток: поднимать есть что, платить пока не за что.
            resumable=bool(count('pending')+count('failed')) or total==0,
            blocks=sorted(broken),details=[broken[key] for key in sorted(broken)])
    def resume_state(self,id):
        """Всё, что нужно `run`, чтобы продолжить прогон после рестарта.

        Сначала сохранённый снимок (`creation_runs.resume`) — он совпадает с
        тем, чем считались отпечатки секций, поэтому готовые куски попадают в
        кэш и не оплачиваются второй раз. Совсем старые прогоны снимка не
        имеют: для них вход пересчитывается тем же `inputs`, что и при старте.
        """
        row=self.raw(id);draft=row['draft']
        saved={}
        try:saved=json.loads((row.get('resume') if hasattr(row,'get') else row['resume']) or '{}')
        except ValueError:saved={}
        if not isinstance(saved,dict):saved={}
        documents=saved.get('documents');ruleset=saved.get('ruleset');identity=saved.get('identity')
        if documents and ruleset and identity:
            value=self.creation.get(draft);private=self.creation.owned(draft)
            # Множество, как в `inputs`: сравнение «материалы не изменились» ниже
            # сверяет именно множества, и список из JSON давал ложное «изменились».
            return value,private,set(documents),ruleset,identity
        result=json.loads(row['result']) if row['result'] else {}
        metadata=resume_metadata(result)
        ruleset=ruleset or metadata['source_ruleset']
        value=self.creation.get(draft);private=self.creation.owned(draft)
        documents=list(documents or self.creation.allowed_documents(draft))
        if not ruleset or not documents:
            return None
        world_structure=metadata['preparation_mode']!='source_extract'
        try:
            _,_,documents,ruleset,identity,fingerprint=self.inputs(draft,PrepareRequest(
                request_id=row['request'],ruleset=ruleset,consent_to_send=True,world_structure=world_structure))
        except Exception as exc:
            logger.warning('r238: не смог пересчитать вход прогона %s: %s',id,exc)
            return None
        if row['book_fingerprint'] and self.book_hash(documents,ruleset,identity)!=row['book_fingerprint']:
            # Настройки роли или модель изменились — прогон всё равно поднимаем,
            # но готовые секции могут не попасть в кэш. Это лучше, чем тишина.
            logger.warning('r238: книга прогона %s изменилась под рукой, кэш секций может не совпасть',id)
        return value,private,documents,ruleset,identity
    async def resume(self,id,reason='',force=False):
        """Поднять прогон без участия владельца: с того же куска, что и был.

        `force` — это кнопка «добить эти блоки»: у отказавших кусков счётчик
        попыток обнуляется, иначе после третьего отказа кнопка не делала бы
        ничего, а блок оставался бы мёртвым навсегда.
        """
        row=self.raw(id)
        if row['status']=='cancelled':return None
        # Родитель, который успел разделиться и был прерван, а дети приняты,
        # оставался 'interrupted' навсегда: в счётчиках он висел прерванным, а
        # сборка мира ждала «незакрытый блок». Работу сделали дети — статус
        # родителя приводим к правде.
        with self.preparation.connection() as db:
            db.execute("UPDATE creation_parts SET status='split' WHERE status='interrupted' "
                       "AND children IS NOT NULL AND children NOT IN ('','[]')")
            # Обратная беда: кусок помечен разделённым, а детей нет — работа
            # потерялась при обрыве между записью статуса и записью детей.
            # Такой кусок висит «в очереди» вечно; читаем его как обычный блок.
            db.execute("UPDATE creation_parts SET status='queued' WHERE status='split' "
                       "AND (children IS NULL OR children IN ('','[]'))")
        task=self.tasks.get(id)
        if task is not None and not task.done():
            # У прогона уже есть живой воркер: второй воркер = двойная оплата.
            return self.status(id)
        left=self.remaining(id)
        if force:
            self.reset_attempts(id)
            left=self.remaining(id)
        if not left['resumable']:
            # Остатка нет: прогон не «висит». Но если все блоки разобраны, а
            # мира нет — работа осталась в сборке, и её делает `finalize`
            # (модель не зовётся). Так закрывается «частично готово» после
            # смерти процесса на сборке и «структура мира не сошлась»,
            # посчитанная прежней версией движка: прогон получает второй шанс
            # без повторной оплаты блоков.
            if left['total'] and left['done']==left['total']:
                return await self.reassemble(id,left)
            self.close_leftover(id,left)
            return self.status(id)
        state=self.resume_state(id)
        if state is None:
            logger.warning('r238: прогон %s: вход не восстановлен, поднять не могу',id)
            # Без входа прогон не оживёт. Если оставить его в очереди, сторож
            # поднимает его каждую минуту: в журнале навсегда «поднял прогон»,
            # а работы нет. Закрываем честно — куски остаются в кэше.
            self.update(id,status='failed',message='Не удалось восстановить исходную заявку после перезапуска приложения. Загрузите файл ещё раз: готовые блоки повторно не оплачиваются.')
            return None
        value,private,documents,ruleset,identity=state
        logger.warning('r238: поднял прогон %s (%s), готово %s из %s',id,reason or 'вручную',left['done'],left['total'])
        self.update(id,status='running',message='Продолжаю разбор с того же блока. Готовые блоки повторно не оплачиваются.')
        task=asyncio.create_task(self.run(id,value,private,documents,ruleset,identity));self.tasks[id]=task
        task.add_done_callback(lambda done:self.tasks.pop(id,None))
        return self.status(id)
    def reset_attempts(self,id):
        """Вернуть отказавшим блокам право на новые попытки (кнопка «добить»)."""
        with self.preparation.connection() as db:
            db.execute('UPDATE creation_parts SET attempts=0 WHERE status IN ("failed","interrupted") '
                'AND id IN (SELECT part FROM creation_part_links WHERE run=?)',(id,))
    async def retry(self,id,reason='по кнопке «добить»'):
        """«Добить эти блоки»: осознанный повтор только отказавших кусков."""
        return await self.resume(id,reason,force=True)
    def needs_assembly(self,result):
        """Мир из принятых блоков ещё не собирался — или собирался другим кодом."""
        if not result:return True
        try:value=json.loads(result)
        except ValueError:return True
        if value.get('ready'):return False
        # Собранный этим же кодом и не сошедшийся мир второй раз не собираем:
        # повтор ничего не изменит, а на каждый старт уходили бы минуты. Правка
        # движка меняет отпечаток — и миру снова даётся второй шанс.
        return value.get('assembly_build')!=current_build()
    async def resume_pending(self):
        """На старте приложения: всё, что оборвалось, продолжается само."""
        with self.preparation.connection() as db:
            everything=list(db.execute('SELECT id,status,draft,created,result FROM creation_runs ORDER BY created'))
        rows=[row for row in everything if row['status'] in ('queued','running','interrupted')]
        # r240: прогон с полностью принятыми блоками и несобранным миром при
        # старте получает второй шанс. Сборка локальная и ничего не стоит, а
        # «частично готово» на экране — это брошенная подготовка: блоки
        # оплачены, игра не начинается. Берём только последний прогон
        # черновика и только если прошлая сборка его ещё не пробовала, иначе
        # каждый старт заново тратил бы минуты на тот же отказ.
        latest={}
        for row in everything:latest[row['draft']]=row['id']
        waiting=[row for row in everything if row['status'] in ('blocked','partial')
            and row['id']==latest.get(row['draft']) and self.needs_assembly(row['result'])]
        # Один черновик — один живой прогон. Иначе два воркера читают ту же
        # книгу (куски общие, ключ — отпечаток), плодят вдвое больше частей и
        # мешают друг другу двигать счётчик: на экране «в работе», а прогресс
        # стоит. Старые прогоны того же черновика честно закрываются.
        newest={}
        for row in rows:newest[row['draft']]=row['id']
        stale=[row for row in rows if newest.get(row['draft'])!=row['id']]
        rows=[row for row in rows if newest.get(row['draft'])==row['id']]
        for row in stale:
            try:self.update(row['id'],status='cancelled',message='Прогон заменён более новым: книга уже разбирается в нём.')
            except Exception as exc:logger.warning('r238: устаревший прогон %s не закрылся: %s',row['id'],exc)
        raised=[]
        # r245: мир уже собран, а статус остался «частично готово» (живой прогон
        # 2e18c595: 96 из 96 блоков, `playable`, `pending=0`). Продолжать нечего,
        # собирать нечего — на экране это брошенная подготовка, а `reuse_book`
        # такую книгу не видит и разбирает её заново за полную цену. Закрываем
        # прогон тем, что в нём уже лежит: модель не зовётся.
        for row in list(everything):
            if row['status'] not in ('partial','blocked'):continue
            if row['id']!=latest.get(row['draft']):continue
            if not row['result']:continue
            try:value=json.loads(row['result'])
            except ValueError:continue
            if not value.get('ready'):continue
            left=self.remaining(row['id'])
            if left['resumable'] or not left['total'] or left['done']!=left['total']:continue
            try:
                self.update(row['id'],status='ready',
                    message=f'Все {left["total"]} блоков разобраны, мир готов. Статус исправлен на «готово»: продолжать и платить нечего.')
                raised.append(row['id'])
            except Exception as exc:logger.warning('r245: прогон %s не закрылся по готовому миру: %s',row['id'],exc)
        waiting=[row for row in waiting if row['id'] not in raised]
        for row in rows:
            left=self.remaining(row['id'])
            if not left['resumable']:
                # Работа кончилась. Если блоки все приняты, а мира нет — прогон
                # оборвался на сборке; собираем из истории секций, модель не зовём.
                if left['done'] and left['done']==left['total'] and not row['result']:
                    try:
                        await asyncio.to_thread(self.finalize,row['id'])
                        raised.append(row['id'])
                    except Exception as exc:logger.warning('r238: прогон %s не собрался: %s',row['id'],exc)
                    else:continue
                # Поднимать нечего — закрываем честным итогом, а не тишиной.
                if left['total']:self.close_leftover(row['id'],left)
                continue
            try:await self.resume(row['id'],'после перезапуска приложения')
            except Exception as exc:logger.warning('r238: прогон %s не поднялся: %s',row['id'],exc)
            else:raised.append(row['id'])
        for row in waiting:
            left=self.remaining(row['id'])
            # Сборка имеет смысл только когда работа по блокам кончилась и
            # доделывать нечего: иначе это не сборка, а повторный разбор, и он
            # стоит денег — такой прогон здесь не трогаем.
            if left['resumable'] or not left['total'] or left['done']!=left['total']:continue
            try:
                await asyncio.to_thread(self.finalize,row['id'])
                raised.append(row['id'])
            except Exception as exc:logger.warning('r240: прогон %s не собрался при старте: %s',row['id'],exc)
        return raised
    def finalize(self,id):
        """Собрать мир из уже проверенных блоков, если прогон оборвался на сборке.

        Все секции приняты и оплачены, но `run()` умер до сборки: на экране
        «частично готово», игра не начинается. Берём секции из истории, ничего
        заново не спрашиваем у модели.
        """
        row=self.raw(id)
        with self.preparation.connection() as db:
            batches=[json.loads(r[0]) for r in db.execute(
                'SELECT body FROM creation_batch_history WHERE run=? ORDER BY batch',(id,))]
        if not batches:return None
        saved=json.loads(row['resume'] or '{}')
        request=PrepareRequest(request_id=row['request'],ruleset=saved.get('ruleset'),
            world_structure=True,consent_to_send=True)
        value,private,documents,ruleset,identity,fingerprint=self.inputs(row['draft'],request)
        module='prepared-'+row['draft'].replace('-','')[:24]
        result=PreparedWorldValidator(self.library,documents).compile(batches,active_mechanics(),
            module=module,complete=True,source_ruleset=ruleset)
        from .first_playable import entry_result
        result=entry_result(self.library,documents,result,ruleset,module)
        previous=json.loads(row['result']) if row['result'] else {}
        metadata=resume_metadata(previous)
        if metadata['source_ruleset'] is None:metadata['source_ruleset']=ruleset
        result['edition_evidence']=edition_evidence(self.library,documents)
        metadata['edition_evidence']=dict(edition=result['edition_evidence']['edition'],
            pages=result['edition_evidence']['pages'],ambiguous=result['edition_evidence']['ambiguous'])
        result['preparation_metadata']=metadata
        result['start_contract']=dict(documents=sorted(documents),
            description_sha256=hashlib.sha256(value['description'].encode()).hexdigest(),world_structure=True)
        result['assembled_from_history']=True
        result['assembly_build']=current_build()
        self.apply_result(row['draft'],private['body']['adventure_bundle'],ruleset,result)
        # «Вызовов модели не было» относится к самой сборке, а не к прогону: этот
        # id мог отработать всю книгу и заплатить за 600 вызовов, и рядом со
        # счётчиком цены такая фраза читалась как ложь (r246).
        message=('Мир собран из готовых блоков: книга разобрана целиком, сама сборка прошла без вызовов модели.'
            if result['ready'] else 'Блоки разобраны, но структура мира не сошлась — покажу, чего не хватает.')
        self.update(id,status='ready' if result['ready'] else 'blocked',
            message=message,result=json.dumps(result,ensure_ascii=False))
        return self.raw(id)
    async def reassemble(self,id,left=None):
        """Собрать мир из готовых блоков по запросу: модель не зовётся.

        Все блоки приняты, но сборки не было (процесс умер) или она посчитана
        прежней версией движка. Работа локальная и бесплатная, поэтому её можно
        повторить — в отличие от разбора, где повтор стоит денег.
        """
        task=self.tasks.get(id)
        if task is not None and not task.done():return self.status(id)
        self.update(id,status='running',message='Собираю мир из готовых блоков. Вызовов модели не будет.')
        async def work():
            try:
                await asyncio.to_thread(self.finalize,id)
            except Exception as exc:
                logger.warning('r240: прогон %s не собрался из готовых блоков: %s',id,exc)
                try:self.close_leftover(id)
                except Exception:logger.exception('r240: прогон %s не закрылся после неудачной сборки',id)
        task=asyncio.create_task(work());self.tasks[id]=task
        task.add_done_callback(lambda done:self.tasks.pop(id,None))
        return self.status(id)
    def close_leftover(self,id,left=None):
        """Закрыть прогон, у которого не осталось работы: итог, а не тишина.

        Сторож зовёт это, когда в `running`/`interrupted` лежит прогон без
        незавершённых кусков: поднимать нечего, но и оставлять его вечно
        «в работе» нельзя — на экране это выглядит как брошенная подготовка.
        """
        row=self.raw(id)
        if row['status'] in TERMINAL and row['status']!='interrupted':return row
        left=left or self.remaining(id)
        status='partial' if left['done'] else 'failed'
        self.update(id,status=status,message=self.partial_message(id,left))
        return self.raw(id)
    def partial_message(self,id,left):
        """Честный итог: сколько готово, какие блоки не разобрались и почему."""
        details=left.get('details') or []
        listing='; '.join(('блок '+str(item['block'])+((' ('+item['pages']+')') if item.get('pages') else '')
            +': '+(item.get('message') or item.get('reason') or 'отказ без причины')) for item in details[:12])
        more=(' и ещё '+str(len(details)-12)) if len(details)>12 else ''
        if left['done'] and left['done']==left['total'] and not details:
            # Все блоки разобраны, а мира нет: остатка работы нет, повторять
            # нечего — но и «остальные в очереди, продолжу сам» тут ложь,
            # продолжать никто не будет. Честно называем, что случилось.
            return (f'Все {left["total"]} блоков разобраны, но мир из них не собрался. '
                'Соберите мир из готовых блоков — повторный разбор не нужен и не оплачивается.')
        if left['done'] and details:
            return (f'Готово {left["done"]} из {left["total"]} блоков. {len(details)} не разобрал'
                f'{"ся" if len(details)==1 else "ись"}: '+listing+more+'. Кнопка «Добить» повторит только их.')
        if left['done']:
            return f'Готово {left["done"]} из {left["total"]} блоков, остальные в очереди. Продолжу сам.'
        if details:
            return ('Ни один блок не разобрался ('+str(left['total'])+' шт.). '+listing+more+'. Исходники сохранены.')
        return f'Ни один блок не разобрался ({left["total"]} шт.). Исходники сохранены, попытки исчерпаны.'
    def update(self,id,**values):
        if not set(values)<={'status','done','total','message','result','progress','worker','heartbeat','resume','playable'}:raise ValueError('Недопустимый прогресс')
        if values.get('status') in TERMINAL and 'worker' not in values:
            # У завершённого прогона воркера нет; сердцебиение ставим сейчас,
            # чтобы сторож не считал финал простоем и не поднимал его снова.
            values['worker']='';values.setdefault('heartbeat',time.time())
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
    def book_hash(self,documents,ruleset,identity):
        """Отпечаток исходной книги: текст и то, чем он читался.

        Ни черновика, ни описания здесь нет намеренно. Отпечаток прогона
        (`creation_runs.fingerprint`) отвечает на вопрос «этот ли запрос», и
        поэтому та же книга в другом черновике получала новый отпечаток и
        оплачивалась заново.
        """
        return hashlib.sha256(json.dumps(dict(documents=sorted(documents),ruleset=ruleset,
            prompt=identity.get('prompt'),prompt_sha256=identity.get('prompt_sha256'),
            model=identity.get('model'),provider=identity.get('provider'),
            wire_contract=identity.get('wire_contract'),mechanics=active_mechanics()),
            sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    def reuse_book(self,draft,value,private,documents,ruleset,identity,fingerprint,book,request):
        """Та же книга уже разобрана: собрать мир из сохранённых секций.

        Секции берутся из `creation_batch_history` и `creation_part_links`
        прошлого прогона, поэтому модель не вызывается вовсе. Возвращает id
        нового прогона или None, если готового разбора нет.
        """
        if not documents or not book:return None
        if identity.get('wire_contract')!='source-world-v1':return None
        # r245: `partial` с полными счётчиками — это книга, разобранная целиком,
        # у которой сборка мира закрылась не тем статусом. Дети разделённых
        # секций приняты (done=total), поэтому разбор полон, и второй раз за
        # него платить нельзя: на живой базе 06.11 такая книга стоила повторный
        # разбор в 17 вызовов и 41 минуту ожидания.
        with self.preparation.connection() as db:
            row=db.execute('SELECT * FROM creation_runs r WHERE r.book_fingerprint=? AND r.status IN ("ready","blocked","partial") '
                'AND r.total>0 AND r.done=r.total AND EXISTS(SELECT 1 FROM creation_batch_history h WHERE h.run=r.id) '
                'ORDER BY r.created DESC LIMIT 1',(book,)).fetchone()
            if row is None:return None
            batches=[json.loads(r[0]) for r in db.execute('SELECT body FROM creation_batch_history WHERE run=? ORDER BY batch',(row['id'],))]
        if not batches:return None
        module='prepared-'+draft.replace('-','')[:24]
        result=PreparedWorldValidator(self.library,documents).compile(batches,active_mechanics(),
            module=module,complete=row['done']==row['total'],source_ruleset=ruleset)
        from .first_playable import entry_result
        result=entry_result(self.library,documents,result,ruleset,module)
        previous=json.loads(row['result']) if row['result'] else {}
        metadata=resume_metadata(previous)
        if metadata['source_ruleset'] is None:metadata['source_ruleset']=ruleset
        result['edition_evidence']=edition_evidence(self.library,documents)
        metadata['edition_evidence']=dict(edition=result['edition_evidence']['edition'],
            pages=result['edition_evidence']['pages'],ambiguous=result['edition_evidence']['ambiguous'])
        result['preparation_metadata']=metadata
        # Отпечаток описания — от ТЕКУЩЕГО черновика: мир проверен по этой книге,
        # а описание игрока его не переписывает.
        result['start_contract']=dict(documents=sorted(documents),
            description_sha256=hashlib.sha256(value['description'].encode()).hexdigest(),world_structure=True)
        result['reused_from']=row['id']
        message='Эта книга уже разобрана, начинаем сразу. Секции взяты из сохранённого прогона — вызовов модели не было.'
        self.apply_result(draft,private['body']['adventure_bundle'],ruleset,result)
        id=str(uuid.uuid4())
        with self.preparation.connection() as db:
            db.execute('INSERT INTO creation_runs(id,draft,request,fingerprint,status,done,total,message,result,created,book_fingerprint,reused) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(id,draft,request.request_id,fingerprint,
                'ready' if result['ready'] else 'blocked',row['done'],row['total'],message,
                json.dumps(result,ensure_ascii=False),time.time(),book,1))
            db.execute('INSERT INTO creation_batch_history SELECT ?,batch,body FROM creation_batch_history WHERE run=?',(id,row['id']))
            db.execute('INSERT INTO creation_part_links SELECT ?,part,ordinal FROM creation_part_links WHERE run=?',(id,row['id']))
        return id
    async def start(self,draft,request):
        value,private,documents,ruleset,identity,fingerprint=self.inputs(draft,request)
        book=self.book_hash(documents,ruleset,identity)
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
        # r236: та же книга уже разобрана — второй раз за неё не платим.
        reused=self.reuse_book(draft,value,private,documents,ruleset,identity,fingerprint,book,request)
        if reused:return self.status(reused)
        metadata=PreparationResumeMetadata(preparation_mode=identity['wire_contract']=='source-world-v1' and 'world_structure' or 'source_extract',source_ruleset=ruleset).model_dump()
        id=str(uuid.uuid4())
        with self.preparation.connection() as db:
            db.execute('INSERT INTO creation_runs(id,draft,request,fingerprint,status,result,created,book_fingerprint) VALUES(?,?,?,?,"queued",?,?,?)',(id,draft,request.request_id,fingerprint,json.dumps(dict(preparation_metadata=metadata)),time.time(),book))
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
        # Read a bounded cursor, not the complete book into memory. A section ends
        # where the book ends a chapter: a stitched cut breaks entities named on
        # both sides of the seam. A chapter shorter than half the budget rides with
        # its neighbour, so a bestiary of one-line entries does not become one paid
        # call per entry.
        #
        # r237: блок наполняется до предела ответа, а не режется по первой же
        # главе. Прежнее правило («новая глава — новый блок, как только набралось
        # 3 000 знаков») на книге 1,25 млн знаков давало 281 вызов вместо 33:
        # триста оплаченных ответов на текст, который модель прочла бы за
        # тридцать. Граница главы теперь — предпочтение, а не приказ.
        from .preparation_format import _page_parts,group_sections
        from .preparation_limits import limits
        cfg=limits();limit=cfg['section_characters'];floor=max(cfg['min_characters'],int(limit*cfg['heading_floor']))
        parts=[];complete=True
        with self.library.db() as db:
            for document in sorted(documents):
                doc=db.execute('SELECT format,quality FROM documents WHERE id=?',(document,)).fetchone()
                if not doc:raise ValueError('Выбранный источник не найден')
                quality=json.loads(doc['quality'])
                # Blank/title/map pages do not prove missing *text*. Selected
                # image-only sources still fail the no-text guard below.
                if quality.get('partial_pages'):complete=False
                pages=[dict(document=document,page=row['number'],printed_page=row['label'],text=row['content'])
                    for row in db.execute('SELECT number,label,content FROM pages WHERE document=? ORDER BY number',(document,))]
                parts.extend(_page_parts(pages,limit))
        if parts:
            for batch in group_sections(parts,limit,floor):yield batch,complete
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
    def stored_roots(self,id):
        """Верхние куски уже построенного плана: их дети в план не входят."""
        with self.preparation.connection() as db:
            rows=[dict(r) for r in db.execute('SELECT p.id,p.children,l.ordinal FROM creation_parts p JOIN creation_part_links l ON l.part=p.id WHERE l.run=? ORDER BY l.ordinal',(id,))]
        children={child for row in rows for child in json.loads(row['children'] or '[]')}
        return [row['id'] for row in rows if row['id'] not in children]
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
        leaves=self.leaves(roots);done=sum(p['status']=='validated' for p in leaves);total=len(leaves)
        # Голос воркера пишется здесь же: это единственное место, которое
        # вызывается после каждого куска, и сторожу этого достаточно, чтобы
        # отличить «долго думает» от «процесс умер».
        self.heartbeat(id)
        started=self.started.get(id);now=time.monotonic();elapsed=round(now-started,1) if started else None
        # Оценка остатка — по средней цене уже готового блока. Пока готовых нет,
        # честнее молчать (None), чем показывать ноль и обещать «сейчас».
        eta=round(elapsed/done*(total-done),1) if started and done and total>done else None
        progress=dict(phase=self.phases.get(id,'extracting'),done=done,total=total,
            failed=sum(p['status']=='failed' for p in leaves),elapsed_s=elapsed,eta_s=eta,
            section=self.cursor.get(id),concurrency=self.concurrency)
        self.update(id,done=done,total=total,message=f'Обработаны блоки: {done} / {len(leaves)}. Результаты сохранены.',
            progress=json.dumps(progress,ensure_ascii=False))
        return leaves

    def prime_batches(self,roots,count):
        """Блоки начала книги — только когда начало покрыто целиком.

        Пока хоть один лист первых `count` блоков не принят, возвращается None:
        вход собирается по факту ответов модели, а не по обещанию.
        """
        chosen=[]
        for root in roots[:count]:
            leaves=self.leaves([root])
            if not leaves or any(leaf['status']!='validated' for leaf in leaves):return None
            chosen.extend(leaves)
        if not chosen:return None
        def order(part):
            pages=json.loads(part['source']).get('pages') or []
            return (pages[0].get('page') or 0,pages[0].get('offset') or 0) if pages else (0,0)
        chosen.sort(key=order)
        characters=sum(len(p['text']) for part in chosen for p in json.loads(part['source'])['pages'])
        # `complete` у проверки мира значит «у отправленных страниц есть текст»,
        # а не «книга прочитана вся». Вход оценивается по себе: непрочитанные
        # главы — это не потерянные страницы, и передавать сюда False значило бы
        # навсегда закрыть вход (проверка мира помечает такой источник дырой).
        readable=all(json.loads(part['source']).get('readable') for part in chosen)
        return [PreparationBatch.model_validate_json(part['body']) for part in chosen],characters,readable

    def try_playable(self,id,draft,bundle,documents,ruleset,identity,roots):
        """Глава 1 собрана — открыть вход, не дожидаясь остальной книги.

        Возвращает True, если вход применён: черновик подготовлен, а прогон
        продолжает читать книгу. Полный мир заменит этот вход только если
        кампания ещё не начата.
        """
        if self.prime.get(id) or not documents:return False
        # Играть можно только по структуре мира: компактный разбор имён не даёт
        # ни стартовой сцены, ни маршрута, и «можно играть» на нём было бы ложью.
        if identity.get('wire_contract')!='source-world-v1':return False
        with self.prime_lock:
            if self.prime.get(id):return False
            module='prepared-'+draft.replace('-','')[:24]
            for count in range(1,min(PLAYABLE_ROOTS,len(roots))+1):
                chosen=self.prime_batches(roots,count)
                if chosen is None:return False
                batches,characters,readable=chosen
                try:
                    result=PreparedWorldValidator(self.library,documents).compile(batches,active_mechanics(),
                        module=module,complete=readable,source_ruleset=ruleset)
                    # Книга прочитана не вся: даже если проверка пропустила этот
                    # кусок как «готовый мир», открываем ТОЛЬКО вход, иначе игрок
                    # получит мир без остальных глав и без границы.
                    if result.get('ready'):result['ready']=False
                    from .first_playable import entry_result
                    result=entry_result(self.library,documents,result,ruleset,module)
                    if not result.get('ready'):
                        # Молчаливое «вход не открылся» стоит часов ожидания:
                        # в журнале должна остаться причина, по которой глава 1
                        # не годится в стартовую сцену.
                        reason=(result.get('entry_readiness') or {}).get('reason','')
                        tried=getattr(self,'prime_reason',None)
                        if tried is None:tried=self.prime_reason={}
                        if tried.get((id,count))!=reason:
                            tried[(id,count)]=reason
                            logger.warning('r237: вход по %s блок(ов) не собирается: %s',count,reason[:160])
                        continue
                    result['playable']=dict(roots=count,blocks=len(batches),characters=characters,ready_at=time.time())
                    result['start_contract']=dict(documents=sorted(documents),
                        description_sha256=hashlib.sha256(self.creation.owned(draft)['body']['description'].encode()).hexdigest(),
                        world_structure=identity['wire_contract']=='source-world-v1')
                    if not self.creation.owned(draft)['campaign']:
                        self.apply_result(draft,bundle,ruleset,result)
                    left=self.remaining(id)
                    self.update(id,playable=1,result=json.dumps(result,ensure_ascii=False),
                        message=f'Можно играть: готово {left["done"]} из {left["total"]}, остальное готовится.')
                    self.prime[id]=True
                    logger.warning('r237: глава 1 готова (%s блок(ов), %s знаков) — вход открыт, готово %s из %s',
                        count,result['playable']['characters'],left['done'],left['total'])
                    return True
                except Exception as exc:
                    # Вход не открылся — это не ошибка прогона: книга читается
                    # дальше, а вход попробуем на следующем блоке.
                    logger.warning('r237: вход по %s блоку(ам) пока не собирается: %s',count,exc)
            return False
    async def run(self,id,value,private,documents,ruleset,identity):
        draft=value['id'];bundle=private['body']['adventure_bundle']
        alive=None
        try:
            # Снимок входа: по нему прогон поднимается после рестарта тем же
            # `inputs`, с теми же отпечатками секций. Без него продолжение
            # пересчитывает вход заново, и готовые куски могут не попасть в кэш.
            self.update(id,status='running',message='AI структурирует источники. Сценарий пока не готов.',
                resume=json.dumps(dict(documents=list(documents),ruleset=ruleset,identity=identity),ensure_ascii=False))
            self.started[id]=time.monotonic();self.phases[id]='reading'
            alive=asyncio.create_task(self.keepalive(id))
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
            # План уже есть — продолжаем его, а не строим заново. Новый план на
            # каждом перезапуске плодил куски (291 -> 682 на одной книге) и
            # двигал итог: на экране прогресс прыгал назад, памяти уходило вдвое
            # больше, а часть блоков читалась повторно.
            stored=self.stored_roots(id)
            if stored:roots=stored
            else:
                for ordinal,(pages,readable) in enumerate(self.source_batches(documents)):
                    # Пустой блок не становится платным вызовом и не занимает место
                    # в плане: «страница есть» ≠ «на странице есть текст».
                    # Авторский замысел — исключение: у него страниц нет вовсе,
                    # пустой блок и есть весь вход, и выбросить его — значит не
                    # собрать мир из описания совсем.
                    if documents and not any((p.get('text') or '').strip() for p in pages):continue
                    roots.append(self.ensure_part(id,identity,pages,readable,ordinal*1000))
            self.phases[id]='extracting'
            self.refresh_progress(id,roots)
            from .preparation_format import diagnostics,split_pages,source_input
            from .preparation_limits import limits
            # Сколько секций читать одновременно: секции независимы, и ожидание
            # ответа модели не должно складываться в очередь.
            self.concurrency=limits()['concurrency']
            from .cloud import CloudUnavailable
            from .cloud_cost import BudgetExceeded
            stop_for_budget=False
            async def process(part_id,depth=0,index=0):
                nonlocal stop_for_budget
                part=self.part(part_id);source=json.loads(part['source']);pages=source['pages']
                self.cursor[id]=index+1
                children=json.loads(part['children'])
                if children:
                    for child in children:
                        with self.preparation.connection() as db:db.execute('INSERT OR IGNORE INTO creation_part_links VALUES(?,?,?)',(id,child,index*1000+depth))
                        if not stop_for_budget:await process(child,depth+1,index)
                    return
                if part['status']=='validated' or stop_for_budget:return
                if self.raw(id)['status']=='cancelled':raise asyncio.CancelledError()
                existing=[e['key'] for p in self.leaves(roots) if p['status']=='validated' for e in json.loads(p['body'])['entities']][-300:]
                rendered,spans,wire_schema=source_input(pages) if documents else (pages,{},PreparationBatch.model_json_schema())
                payload=dict(task='campaign-prepare-v1',description=value['description'],ruleset=ruleset,pages=rendered,batch=index,original=not bool(documents),contracts=schemas,item_catalog=[dict(id=k,name=v['name']) for k,v in CATALOG.items()],existing_keys=existing,response_budget=dict(max_entities=limits()['entities_per_call'],max_quote_characters=240,compact=True))
                if documents and identity['wire_contract']!='source-world-v1':
                    from .source_extraction import request as source_request
                    payload,spans,wire_schema=source_request(pages,value['description'],ruleset,index,existing)
                def part_metric(text,report,repairs_count):
                    """Метрика блока в том же журнале, что и остальные вызовы модели."""
                    fresh=[m for m in self.llm.metrics if object_id(m) not in metric_ids]
                    value=fresh[-1].copy() if fresh else {}
                    value.setdefault('schema_mode','none')
                    value['schema_modes']=list(dict.fromkeys(m.get('schema_mode') or 'none' for m in fresh)) or ['none']
                    value['repairs']=repairs_count;value['calls']=len(fresh)
                    # Байты всего потока — максимум по вызовам блока: удачный ремонт
                    # короче основного ответа и не должен скрывать, что основной
                    # ответ уже подошёл к потолку.
                    value['stream_bytes']=max((int(m.get('stream_bytes') or 0) for m in fresh),default=0)
                    # Потолок — это реальный бюджет роли и локальный предел,
                    # а не число 2048 из головы: по нему видно запас ответа.
                    value['max_tokens']=value.get('output_limit_tokens')
                    value['byte_limit']=(value.get('stream_limits') or {}).get('bytes')
                    value['bytes']=len(text.encode('utf8'))
                    value['section']=index;value['part']=part_id[:16];value['split_depth']=depth
                    value['section_characters']=sum(len(p.get('text') or '') for p in pages)
                    value['schema_dropped_keys']=list(report.get('schema_dropped_keys') or [])
                    value['duplicate_keys']=list(report.get('duplicate_keys') or [])
                    value['duration_ms']=round(sum(m.get('duration_ms') or 0 for m in fresh),2)
                    return value

                def saved_answer(text):
                    """Ответ в wire-формате этого блока: (модель, изменения, отчёт).

                    Книга иногда отвечает компактным контрактом `entries`, хотя
                    просили структуру мира: `parse_answer` переключается по форме
                    сам, а не по режиму, иначе такой блок падал бы зря.
                    """
                    if documents:
                        from .source_extraction import parse_answer
                        return parse_answer(text,spans,ruleset)
                    from .preparation_format import read as read_batch
                    return read_batch(text,spans)
                if part['status']=='failed' and part['raw']:
                    try:
                        # Спасение сохранённого ответа бесплатно: тот же wire-формат
                        # и тот же санитайзер, что и при живом вызове.
                        saved,changes,saved_report=saved_answer(part['raw'])
                    except ValueError:pass
                    else:
                        self.part_update(part_id,status='validated',body=saved.model_dump_json(),diagnostic=json.dumps(
                            [dict(field='$',reason='local_repair',message=r) for r in changes]
                            +[dict(field=path,reason='schema_dropped_key',message='Ключа нет в схеме контракта') for path in saved_report['schema_dropped_keys']]))
                        self.refresh_progress(id,roots);return
                # r238: три попытки на блок, а не одна. Первая идёт с полной
                # задачей, вторая и третья — с урезанной (`max_entities` вдвое
                # меньше): длинный ответ ломается чаще, и «то же самое ещё раз»
                # стоит денег, не меняя причину отказа. После третьей блок
                # остаётся `failed`, остальные едут дальше, а в конце прогона
                # владелец видит честный итог и кнопку «добить эти блоки».
                cfg=limits()
                base_entities=int((payload.get('response_budget') or {}).get('max_entities') or cfg['entities_per_call'])
                attempt=int(part['attempts'] or 0)
                provider_attempt=0
                while attempt<MAX_ATTEMPTS:
                    attempt+=1
                    if attempt>1 and isinstance(payload.get('response_budget'),dict):
                        # Урезанная задача: меньше сущностей за ответ — короче
                        # ответ, меньше шансов снова не пройти схему.
                        payload['response_budget']['max_entities']=max(3,base_entities//(2**(attempt-1)))
                    raw='';metric_ids={object_id(m) for m in self.llm.metrics};metric={}
                    # Состояние блока нужно и в обработчике ошибки: иначе упавший
                    # ремонт пишет repairs=0 и теряет ключи, которые модель выдумала.
                    repairs_count=0;report={}
                    self.part_update(part_id,status='running',attempts=attempt)
                    try:
                        with self.llm.scope('preparation:'+draft,id+':'+part_id[:16]):
                            world_instruction=WORLD_INSTRUCTION if identity['wire_contract']=='source-world-v1' else ''
                            async def generate(system,user):
                                # Потолок считается на каждый ответ отдельно: ремонт
                                # не наследует длину первого ответа и не упирается в
                                # него раньше времени.
                                pieces=[];length=0
                                async with aclosing(self.llm.stream(system,user,mode='preparation_world',grammar=wire_schema,source_extract=bool(documents) and identity['wire_contract']!='source-world-v1')) as stream:
                                    async for chunk in stream:
                                        length+=len(chunk)
                                        if length>512000:raise PreparationProblem('Ответ блока превышает безопасный размер')
                                        pieces.append(chunk)
                                return ''.join(pieces)
                            raw=await generate(world_instruction,json.dumps(payload,ensure_ascii=False))
                            try:result,changes,report=saved_answer(raw)
                            except ValueError as first:
                                # Ремонт — только для ошибок структуры pydantic:
                                # обрезанный по max_tokens ответ и неизвестная
                                # ссылка на фрагмент повтором не лечатся, за них
                                # платить второй раз незачем.
                                if not hasattr(first,'errors'):raise
                                # Один ремонт и только один: ошибки pydantic построчно
                                # и прошлый ответ. Дальше — честная ошибка блока.
                                dropped_first=list(getattr(first,'report',{}).get('schema_dropped_keys') or [])
                                repaired=await generate(REPAIR_INSTRUCTION,repair_input(raw,first))
                                try:result,changes,report=saved_answer(repaired)
                                except ValueError as second:
                                    raw=repaired;repairs_count=1
                                    # Даже провал ремонта сохраняет, что модель
                                    # придумала: иначе метрика врёт о причине.
                                    second_report=getattr(second,'report',{}) or {}
                                    report=dict(second_report,schema_dropped_keys=dropped_first+list(second_report.get('schema_dropped_keys') or []))
                                    raise second from first
                                changes=[*changes,'model_repair'];repairs_count=1
                                raw=repaired
                                # Потери первого ответа не исчезают от того, что ремонт
                                # удался: именно они показывают, что модель придумывает.
                                report=dict(report,schema_dropped_keys=dropped_first+list(report.get('schema_dropped_keys') or []))
                            metric=part_metric(raw,report,repairs_count)
                            diagnostic=[dict(field='$',reason='format_normalization',message=r) for r in changes]+[
                                dict(field=path,reason='schema_dropped_key',message='Ключа нет в схеме контракта') for path in report['schema_dropped_keys']]
                            # Ответ принят и оплачен — он остаётся целым. Раньше
                            # здесь делилась КАЖДАЯ секция, чей поток занял больше
                            # 70 % байтового потолка, а поток состоит в основном из
                            # SSE-обрамления (≈290 байт на событие): на живой книге
                            # это резало здоровые ответы 78–86 КБ и удваивало счёт
                            # вызовов. Дети появляются только по факту отказа —
                            # обрыва по потолку (ниже) или finish_reason=length.
                            cap=(metric.get('stream_limits') or {}).get('bytes');wire=int(metric.get('stream_bytes') or 0)
                            if cap and wire>cap*cfg['headroom']:
                                metric['near_byte_limit']=round(wire/cap,3)
                            self.part_update(part_id,status='validated',body=result.model_dump_json(),raw=raw,diagnostic=json.dumps(diagnostic),
                                metric=json.dumps(metric))
                            self.refresh_progress(id,roots);await asyncio.sleep(0);return
                    except asyncio.CancelledError:
                        self.part_update(part_id,status='interrupted',raw=raw);raise
                    except Exception as exc:
                        metric=part_metric(raw,report,repairs_count)
                        issue=diagnostics(exc)
                        # Потолок байтов/строк/событий — тот же сигнал, что и
                        # finish_reason=length: ответ не влез. Дробим, а не сдаёмся.
                        local_code=getattr(exc,'error_code',None) or metric.get('error_code')
                        limit_hit=bool(metric.get('hit_token_limit')) or local_code in LIMIT_CODES
                        # limit_code — только наш потолок ответа. Сетевой 500,
                        # таймаут соединения и отказ схемы потолком не являются:
                        # они остаются в error_code и не раздувают limit_hits.
                        metric['limit_code']=(local_code if local_code in LIMIT_CODES else 'hit_token_limit') if limit_hit else None
                        metric['split_depth']=depth
                        # Ошибка ФОРМАТА (pydantic/оборванный JSON) — тоже повод дать
                        # секции второй шанс меньшим куском: длинный ответ чаще ломается,
                        # а неизвестная ссылка на фрагмент или лимит расходов — нет.
                        structure_error=(not limit_hit and not isinstance(exc,(CloudUnavailable,BudgetExceeded))
                            and isinstance(exc,ValueError) and (hasattr(exc,'errors') or isinstance(exc,json.JSONDecodeError)))
                        # Деление по ошибке формата ограничено отдельным, маленьким
                        # пределом: детерминированная ошибка (дубль key, name>120)
                        # половинами не лечится, а полное дерево до depth=3 — это
                        # 15 узлов и до 30 оплаченных вызовов с ремонтами на ровном месте.
                        structure_split=structure_error and depth<min(cfg['structure_split_depth'],cfg['max_split_depth'])
                        # Порог деления у потолка ниже обычного: упирается не
                        # размер источника, а размер ОТВЕТА на него. Живой прогон
                        # показал фрагмент в 3,1 тыс. знаков, который не влез в
                        # потолок; при пороге 2 тыс. его уже нельзя было делить, и
                        # две такие страницы запирали весь мир. Делим смелее, но
                        # не мельче 500 знаков: дальше цитата теряет смысл.
                        floor=cfg['min_characters'] if not limit_hit else max(500,cfg['min_characters']//4)
                        children_pages=split_pages(pages,floor) if structure_split or (limit_hit and depth<cfg['max_split_depth']) else []
                        if limit_hit:
                            issue=[dict(field='$',reason='section_split' if children_pages else 'source_section_too_large',
                                message=section_refusal(pages,metric,floor,split=bool(children_pages)))]
                        elif structure_error and children_pages:
                            issue=[dict(field='$',reason='section_structure_error',
                                message='Ответ не прошёл проверку структуры ('+diagnostics(exc)[0]['reason']+'): секция ('+page_span(pages)+') разделена и перечитана по частям.')]
                        elif isinstance(exc,(CloudUnavailable,BudgetExceeded)) or not metric.get('calls'):
                            issue=[dict(field='$',reason='provider_or_budget',message=str(exc)[:300] if isinstance(exc,(CloudUnavailable,BudgetExceeded)) else 'Вызов блока не выполнен')]
                        # Попытка записывается как есть — с причиной и метрикой:
                        # по истории видно, что именно повторялось и во что встало.
                        self.part_update(part_id,status='failed',raw=raw,diagnostic=json.dumps(issue,ensure_ascii=False),metric=json.dumps(metric))
                        if metric.get('calls') and hasattr(self.llm,'contract_error') and hasattr(exc,'errors') and not isinstance(exc,(CloudUnavailable,BudgetExceeded)):self.llm.contract_error('invalid_campaign_preparation')
                        if children_pages:
                            # Обрыв по потолку байт и структурный отказ лечатся
                            # делением: повторять кусок тем же размером незачем.
                            child_ids=[self.ensure_part(id,identity,child,source['readable'],index*1000+depth*10+n) for n,child in enumerate(children_pages)]
                            # Причина деления остаётся в метрике родителя: иначе в журнале
                            # видно только листья и не видно, сколько секций резали и почему.
                            self.part_update(part_id,status='split',children=json.dumps(child_ids),metric=json.dumps(metric),diagnostic=json.dumps(issue,ensure_ascii=False))
                            for child in child_ids:
                                if not stop_for_budget:await process(child,depth+1,index)
                            break
                        # Отказ провайдера — не приговор блоку. «Зеркало модели
                        # временно недоступно» (503 внутри SSE) приходит пачками
                        # и через полминуты уходит само: вызов не оплачен, работы
                        # не сделано, значит ждём и пробуем тот же кусок снова,
                        # не тратя на это попытки формата. Иначе пять потоков
                        # превращаются в 69% «failed» на ровном месте.
                        transient_code=str(getattr(exc,'error_code','') or '')
                        transient_text=str(exc).casefold()
                        transient=(transient_code in ('http_502','http_503','http_504','timeout')
                            or any(m in transient_text for m in ('503','502','504','временно недоступн','timeout','timed out','connection','соединени')))
                        provider_issue=(isinstance(exc,(CloudUnavailable,BudgetExceeded)) or not metric.get('calls')) and transient
                        budget_issue=(isinstance(exc,BudgetExceeded) or 'budget' in str(exc).casefold()
                            or 'общий подтверждённый лимит' in str(exc).casefold())
                        if provider_issue and not budget_issue and provider_attempt<PROVIDER_ATTEMPTS:
                            provider_attempt+=1
                            wait=PROVIDER_BACKOFF[min(provider_attempt-1,len(PROVIDER_BACKOFF)-1)]
                            logger.warning('блок %s (%s): провайдер отказал (%s), жду %s с и повторяю (%s из %s)',
                                part_id[:16],page_span(pages),str(exc)[:80],wait,provider_attempt,PROVIDER_ATTEMPTS)
                            attempt-=1
                            await asyncio.sleep(wait)
                            continue
                        if isinstance(exc,BudgetExceeded) or 'budget' in str(exc).casefold() or 'общий подтверждённый лимит' in str(exc).casefold():
                            stop_for_budget=True;break
                        # Один повтор с урезанной задачей — и только для отказа
                        # ОТВЕТА: сеть, провайдер и потолок расходов повтором не
                        # лечатся, за них платить второй раз нечего. Урезать
                        # есть что только у секции крупнее минимальной части:
                        # огрызок в 1,7 тыс. знаков уже не дробится, и повтор
                        # его тем же куском ничего не изменит.
                        retryable=(bool(metric.get('calls')) and not isinstance(exc,(CloudUnavailable,BudgetExceeded))
                            and not stop_for_budget
                            and sum(len(p.get('text') or '') for p in pages)>cfg['min_characters'])
                        if retryable and attempt<MAX_ATTEMPTS:
                            logger.warning('r238: блок %s (%s): попытка %s из %s не прошла (%s), повторяю с урезанной задачей',
                                part_id[:16],page_span(pages),attempt,MAX_ATTEMPTS,issue[0]['reason'] if issue else 'отказ')
                            continue
                        break
                self.refresh_progress(id,roots)
                await asyncio.sleep(0)
            slots=asyncio.Semaphore(self.concurrency)
            async def staggered(root,index):
                # Подъём ограничен: провайдер не любит шесть запросов на одну книгу,
                # а память на VPS — тем более.
                async with slots:
                    if stop_for_budget:return
                    await process(root,0,index)
                    # r237: закончился блок — если это начало книги, попробовать
                    # открыть вход. Сборка локальная и бесплатная.
                    try:await asyncio.to_thread(self.try_playable,id,draft,bundle,documents,ruleset,identity,roots)
                    except Exception as exc:logger.warning('r237: вход после блока %s не открылся: %s',index+1,exc)
            if len(roots)>1 and self.concurrency>1:
                # Секции независимы: одна упавшая не должна отменять остальные.
                await asyncio.gather(*[staggered(root,index) for index,root in enumerate(roots)],return_exceptions=True)
            else:
                for index,root in enumerate(roots):
                    if not stop_for_budget:
                        await process(root,0,index)
                        try:await asyncio.to_thread(self.try_playable,id,draft,bundle,documents,ruleset,identity,roots)
                        except Exception as exc:logger.warning('r237: вход после блока %s не открылся: %s',index+1,exc)
            self.phases[id]='assembling'
            leaves=self.refresh_progress(id,roots)
            for index,part in enumerate(leaves):
                if part['status']!='validated':continue
                result=PreparationBatch.model_validate_json(part['body'])
                with self.preparation.connection() as db:db.execute('INSERT OR REPLACE INTO creation_batch_history VALUES(?,?,?)',(id,index,result.model_dump_json()))
                batches.append(result.model_dump());complete=complete and json.loads(part['source'])['readable']
            if len(batches)!=len(leaves):
                # Без этих чисел «частично готово» ничего не объясняет: видно,
                # сколько листьев, какие именно не приняты и сколько собралось.
                left_status={}
                for part in leaves:left_status[part['status']]=left_status.get(part['status'],0)+1
                logger.warning('r238: сборка не сошлась: собрано %s, листьев %s (%s)',len(batches),len(leaves),left_status)
                # r238: честный итог. Не «сохранено 27/30», а сколько блоков
                # готово, какие не разобрались и чем это лечится.
                left=self.remaining(id)
                # Числа в базе и в итоге обязаны совпадать: после деления
                # секций листьев больше, чем было в последнем `refresh_progress`,
                # и «готово 96 из 97» рядом с «106 из 108» выглядит как враньё.
                self.update(id,status='partial' if batches else 'failed',done=left['done'],total=left['total'],
                    message=self.partial_message(id,left));return
            if sum(len(json.dumps(b,ensure_ascii=False)) for b in batches)>4000000:raise PreparationProblem('Подготовленный материал слишком велик. Проверенные блоки сохранены.')
            original_document=self.original_document(bundle,value['description']) if not documents else ''
            result=PreparedWorldValidator(self.library,documents,original=not bool(documents),original_document=original_document).compile(batches,active_mechanics(),module='prepared-'+draft.replace('-','')[:24],complete=complete,source_ruleset=ruleset)
            if documents:
                from .first_playable import entry_result
                result=entry_result(self.library,documents,result,ruleset,'prepared-'+draft.replace('-','')[:24])
            result['start_contract']=dict(documents=sorted(documents),description_sha256=hashlib.sha256(value['description'].encode()).hexdigest(),world_structure=identity['wire_contract']=='source-world-v1')
            # Reject stale results if the player changed text or selected files during inference.
            latest=self.creation.get(draft)
            if latest['description']!=value['description'] or set(self.creation.allowed_documents(draft))!=set(documents):raise PreparationProblem('Материалы или описание изменились. Подготовьте новую версию; старый ответ не применён.')
            # r237: если игрок уже начал кампанию по главе 1, полный мир не
            # переписывает черновик под ним: итог сохраняется в прогоне, а
            # начатая кампания остаётся целой.
            if not self.creation.owned(draft)['campaign']:self.apply_result(draft,bundle,ruleset,result)
            message=(result.get('entry_readiness') or {}).get('reason') or ('Приключение подготовлено. Создайте героя.' if result['ready'] else 'Часть сценария не прошла проверку. Неподдержанные важные элементы изолированы; запуск заблокирован.')
            self.update(id,status='ready' if result['ready'] else 'blocked',result=json.dumps(result,ensure_ascii=False),message=message)
        except asyncio.CancelledError:
            self.update(id,status='interrupted',message='Подготовка прервана. Черновик и исходники сохранены.')
            raise
        except Exception as exc:
            # Contract diagnostics are private; avoid quoting book secrets in the player error.
            from .llm import CloudUnavailable,BudgetExceeded
            # А вот в лог причина уходит целиком: «Подготовка не завершена» без
            # следа — это та же тишина, только вежливая.
            logger.exception('r238: прогон %s упал на сборке: %s',id,exc)
            message=str(exc) if isinstance(exc,(CloudUnavailable,BudgetExceeded,PreparationProblem)) else 'Подготовка не завершена. Исходники сохранены; проверьте подключение, лимит расходов или повторите попытку.'
            self.update(id,status='failed',message=message)
        finally:
            # Воркер ушёл — сердцебиение должно замолчать вместе с ним, иначе
            # сторож будет считать живым то, чего уже нет.
            if alive is not None:alive.cancel()
            self.started.pop(id,None);self.cursor.pop(id,None)
