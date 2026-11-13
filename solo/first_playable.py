"""Source-pinned entry stages; a reviewed entry never approves the rest of a book.

Layout policies are deliberately document-specific. Typography alone is not proof
that an arbitrary box is player-facing. Unknown layouts remain closed.
"""
import copy,hashlib,re,sqlite3
from .adventure_contracts import AdventureDefinition
from .prepared_world import observable_opening,observable_sentences,page_paragraph_state
from .world import WorldEvent,WorldLocation,WorldNode,WorldRoute,ScenarioPackage

HOARD='7ad6e955bc095a88c1d57fa68f4f36ff714edbeeb31f362ac59db2ce778adbdf'
OPENING_SHA='c4b7ad1d949faf98fb5b64ee645a97ac99dd9eab480e69930ecb5098294b3111'
BOUNDARY='Переход в Гринест пока закрыт: не проверены боевые профили участников и процедура обязательного первого столкновения (PDF, стр. 10). Можно осмотреть видимое с холма, открыть лист героя или сохранить игру; время сцены не продвигается.'

def stage_of(state):return state.get('adventure',{}).get('definition',{}).get('entry_stage')

def is_stub_description(name,text):
    """True when a place 'description' is only its own name or the host's prompt."""
    value=' '.join(str(text or '').split())
    if not value:return True
    if value.startswith('Вы находитесь в месте «'):return True
    return value.rstrip('.!?…«» ')==' '.join(str(name or '').split()).rstrip('.!?…«» ')

def text_allowed(text):
    # A conservative complete clause. An interpreter label cannot hide movement,
    # a compound action or an attempt to create an object/discovery.
    value=' '.join(text.casefold().replace('ё','е').split()).strip('.! ')
    return bool(re.fullmatch(r'(?:я )?(?:осматриваюсь|оглядываюсь|(?:осматриваю|рассматриваю|наблюдаю) (?:окрестности|дорогу|холм|город|гринест|дым|горящие дома|людей вдали|крепость|крылатый силуэт|небо|поля))',value))

def guard_text(state,text):
    return BOUNDARY if stage_of(state) and not text_allowed(text) else None

def guard_plan(state,plan):
    if not stage_of(state):return
    targets={'','Приближение к Гринесту','дорога','дорогу','холм','город','Гринест','дым','горящие дома','люди вдали','людей вдали','крепость','крылатый силуэт','небо','поля'}
    if state.get('deferred') or any(s.kind!='observe' or s.disposition!='now' or s.after or s.condition or not text_allowed(s.text) or s.target.casefold() not in {t.casefold() for t in targets} for s in plan.steps):
        raise ValueError(BOUNDARY)

def require_full_scene(state):
    if stage_of(state):raise ValueError(BOUNDARY)

def generic_entry(result,page_text=''):
    """The confirmed opening place of any compiled book, or None.

    Nothing here knows which book is being prepared. The question is only whether
    the compile produced a place whose text the source verified, that the source
    itself named as the start, with at least one other reached place. A world that
    cannot answer that stays closed; a world that can is opened by its own facts.
    """
    definition=result.get('definition') or {}
    world=definition.get('world') or {}
    start=(result.get('start') or '').strip()
    places=[p for p in world.get('locations') or [] if (p.get('name') or '').strip()]
    if len(places)<2:return None
    if not start:
        # Источник не назвал стартовое место (глава 1 печатает карту мира, а
        # не «начните здесь»). Вход всё равно открывается — на первом месте с
        # проверенным read-aloud текстом, а если книга таких не печатает, то на
        # первом проверенном месте в порядке книги. Это не выдумка: место и его
        # страница подтверждены; кто и где начинает, решается в игре.
        start=next((p['name'] for p in places if (p.get('description') or '').strip()),'')
        if not start:
            first=sorted((p for p in places if _cited(result,p['name'])),key=lambda p:_cited(result,p['name']).get('page') or 0)
            start=first[0]['name'] if first else ''
        if not start:return None
    node=next((p for p in places if p.get('name')==start),None)
    if node is None:return None
    description=(node.get('description') or '').strip()
    others=[p.get('name') for p in places if p.get('name')!=start]
    scene=[text for text in observable_sentences(node,page_text,others)
        if text.rstrip('.!?…').casefold()!=' '.join(start.split()).casefold()]
    published=False
    if is_stub_description(start,description):
        # Книга, которая не печатает read-aloud (русские издания сплошь и рядом),
        # остаётся без описания: её проза уехала в sealed_text. Публикуем не весь
        # абзац, а предложения без метки тайны и без указаний мастеру
        # (prepared_world.observable_sentences). Иначе у мастера нет ни одного
        # подтверждённого слова о сцене, и любой его ход — выдумка, которую
        # движок справедливо отбрасывает.
        if scene:
            description=observable_opening(node,page_text,others);published=True
        else:
            # Последний исход: книга не даёт ни одного безопасного предложения о
            # своём же стартовом месте. Игрок видит название и нейтральную строку.
            description='Вы находитесь в месте «'+start+'». Что делает ваш герой?'
    # Первый подтверждённый факт места — первое видимое предложение книги: без
    # него пакет мастера (atmosphere + confirmed) пуст, и любой его ход о сцене
    # выглядит выдумкой, которую narrative_grounding справедливо отбрасывает.
    facts=list(node.get('facts') or [])
    if not facts and scene:facts=scene[:1]
    if published or facts!=(node.get('facts') or []):
        # Сцена уходит в мир вместе с этим текстом: иначе проверка входа видит
        # пустое описание места и запирает подтверждение мира.
        world=copy.deepcopy(world)
        for place in world.get('locations') or []:
            if place.get('name')==start:
                place['description']=description
                place['facts']=facts
    proof=_cited(result,start)
    return dict(name=start,description=description,page=proof.get('page') or 1,
        document=proof.get('document') or '',evidence=[proof] if proof else [],world=world,
        published=published,sentences=scene)


def _unresolved_reason(place,page_text,others,page_locked=False):
    """Почему стартовая сцена осталась шаблоном — без чтения книги.

    `page-empty` — проверенной страницы нет вовсе; `page-unreadable` — страница
    закреплена в `entry_stage`, но её текст не открылся; `no-heading` — страница
    не называет место ни заголовком, ни в прозе; `only-gm` — место названо, но
    публичного предложения с ним нет (инструкция ведущему или одно название).
    """
    name=' '.join(str(place.get('name') or '').split())
    _,reason=page_paragraph_state(page_text,name,others)
    if reason=='page-empty' and page_locked:return 'page-unreadable'
    return reason if reason in ('page-empty','no-heading') else 'only-gm'


def publish_entry_scene(result,page_text=''):
    """Publish the verified opening of a prepared world: its text and first fact.

    Миры, собранные до r250, хранят проверенную сцену без текста: описание
    стартового места — название места или шаблон, — а проза страницы в мир не
    попала. Точно так же и свежий разбор может опубликовать текст, но оставить
    `facts` пустым — и тогда пакет мастера (`atmosphere` + `confirmed`) пуст, а
    любой его ход о сцене выглядит выдумкой. Модель для починки не нужна:
    берутся только публичные предложения проверенной страницы, и отпечаток сцены
    пересчитывается по опубликованному тексту — иначе повторная проверка входа
    (adventure_engine.initialize) отвергнет мир, у которого текст не совпал с
    `opening_sha256`. Сам текст при этом остаётся привязан к странице: её номер
    и документ лежат в `entry_stage`, а `opening_source` говорит, откуда взят
    абзац и сколько в нём предложений.

    Ничего не нашлось — мир не трогаем, но и не молчим: в `opening_source`
    появляется `text='unresolved'`, чтобы «починка не сработала» было видно.
    """
    definition=result.get('definition') or {}
    stage=definition.get('entry_stage') or {}
    start=(result.get('start') or '').strip()
    place=next((p for p in (definition.get('world') or {}).get('locations') or []
        if (p.get('name') or '').strip()==start),None)
    if place is None:return result
    stub=is_stub_description(start,place.get('description'))
    if not stub and place.get('facts'):return result
    others=[(p.get('name') or '') for p in (definition.get('world') or {}).get('locations') or []
        if (p.get('name') or '').strip() and (p.get('name') or '').strip()!=start]
    scene=[text for text in observable_sentences(place,page_text,others)
        if text.rstrip('.!?…').casefold()!=' '.join(start.split()).casefold()]
    if not scene and not stub:return result
    description=observable_opening(place,page_text,others) if stub else str(place.get('description') or '')
    if stub and is_stub_description(start,description):
        # Публичного абзаца нет: шаблон остаётся последним исходом, но в
        # результате видно, что именно не нашлось (страница без заголовка,
        # страница-картинка, абзац целиком из материала мастера). Причина
        # берётся у того же поиска, что и абзац, — без чтения книги.
        proof=_cited(result,start)
        locked=bool((stage.get('document') and stage.get('page')) or proof.get('page'))
        result=dict(result)
        result['opening_source']=dict(document=str(stage.get('document') or proof.get('document') or ''),
            page=stage.get('page') or proof.get('page'),text='unresolved',sentences=0,
            reason=_unresolved_reason(place,page_text,others,locked))
        return result
    result=copy.deepcopy(result)
    definition=result['definition']
    place=next(p for p in definition['world']['locations'] if (p.get('name') or '').strip()==start)
    if stub:place['description']=description
    if not place.get('facts'):place['facts']=scene[:1]
    if stub and stage:
        # Отпечаток остаётся отпечатком: он пересчитан по опубликованному тексту,
        # а страница и документ уже лежат в entry_stage.
        definition['entry_stage']['opening_sha256']=hashlib.sha256(description.encode('utf-8')).hexdigest()
    proof=_cited(result,start) if not stage else {}
    result['opening_source']=dict(document=str(stage.get('document') or proof.get('document') or ''),
        page=stage.get('page') or proof.get('page'),
        text='printed-observable' if stub else 'book-text',sentences=len(scene),
        # Якорь к источнику: опубликованный абзац взят из этой страницы, и её
        # отпечаток позволяет проверить текст, не открывая книгу заново.
        page_sha256=hashlib.sha256(page_text.encode('utf-8')).hexdigest() if page_text else None)
    return result


def entry_page_text(library,result,bundle=None):
    """Текст проверенной страницы, на которую ссылается стартовое место.

    `entry_stage` хранит документ и страницу, а сама страница остаётся в
    библиотеке: только оттуда берётся абзац, который книга печатает о месте.
    """
    if library is None:return ''
    # Страница закреплена в `entry_stage`: именно её проверил вход. Цитата
    # провенанса — только запасной путь для мира, у которого stage нет.
    stage=(result.get('definition') or {}).get('entry_stage') or {}
    document=str(stage.get('document') or '');page=stage.get('page')
    if not document or not page:
        start=(result.get('start') or '').strip()
        proof=_cited(result,start) or {}
        document=str(proof.get('document') or '');page=proof.get('page')
    try:page=int(page or 0)
    except (TypeError,ValueError):return ''
    if not document or page<1:return ''
    if bundle:
        # Одна страница по проверенной ссылке: книгу целиком в память не тянем.
        try:return library.page_text(bundle,document,page) or ''
        except (KeyError,ValueError,OSError,AttributeError,sqlite3.Error):return ''
    try:pages=library.page_texts(document)
    except (KeyError,ValueError,OSError,AttributeError,sqlite3.Error):return ''
    return str(pages.get(page) or pages.get(str(page)) or '')


def _cited(result,name):
    """The source citation the compile kept for this place.

    The analysis names places by key ('bell_landing') while the world prints them
    by name ('Bell Landing'), so the citation is looked up on the normalised form
    rather than on the literal string.
    """
    want=re.sub(r'[^0-9a-zа-яё]+','_',name.lower().replace('ё','е')).strip('_')
    for key,entry in (result.get('provenance') or {}).items():
        if not isinstance(entry,dict):continue
        here=re.sub(r'[^0-9a-zа-яё]+','_',str(key).lower().replace('ё','е')).strip('_')
        # Ключ анализа и напечатанное название — две записи одного места:
        # у книги ключ часто латиницей, а имя в мире русское.
        named=re.sub(r'[^0-9a-zа-яё]+','_',str(entry.get('name') or entry.get('canonical_name') or '').lower().replace('ё','е')).strip('_')
        if here!=want and named!=want:continue
        for proof in entry.get('evidence') or []:
            if isinstance(proof,dict):return proof
    return {}


def _page_of(result,name):  # noqa: D401 - kept for the pinned Hoard branch
    """The source page a place was cited from, when the compile kept it."""
    for entry in (result.get('provenance') or {}).values():
        if not isinstance(entry,dict):continue
        for proof in entry.get('evidence') or []:
            if isinstance(proof,dict) and proof.get('page'):return int(proof['page'])
    return 1


def generic_definition(opening,result,ruleset,module):
    """Open the entry scene of any book on the facts the compile already proved.

    The scene is a place, its verified text and an observation boundary, so it is
    edition-neutral: the mechanics preset is chosen separately with the hero. The
    rest of the book stays closed until it is confirmed, exactly as before.
    """
    import hashlib as _hashlib
    from .preparation_pipeline import active_mechanics
    from .progression import book_progression
    progression=result.get('progression')
    name=opening['name'];text=opening['description'];page=opening['page']
    document=''
    for proof in opening['evidence']:
        if isinstance(proof,dict) and proof.get('document'):document=str(proof['document']);break
    if not document:return None
    stage=dict(policy='verified-entry-v1',document=document,page=page,
        opening_sha256=_hashlib.sha256(text.encode('utf-8')).hexdigest(),
        boundary='Переход дальше по книге закрыт: подтверждена только открытая стартовая сцена.')
    # The whole confirmed world travels with the definition: every place the source
    # proved, the transitions between them and the encounters those pages imply.
    # Entering keeps the hero in the opening scene only because that is the one
    # place marked known, not because the rest was thrown away; discarding it would
    # leave a paid preparation with a single room and no way forward.
    world=opening['world']
    locations=[WorldLocation.model_validate(p) for p in (world.get('locations') or [])]
    nodes=[WorldNode.model_validate(n) for n in (world.get('nodes') or [])]
    routes=[WorldRoute.model_validate(r) for r in (world.get('routes') or [])]
    events=[WorldEvent.model_validate(e) for e in (world.get('events') or [])]
    try:
        return AdventureDefinition(id=module,title=name+' — начало',ruleset=active_mechanics(),ruleset_version=1,
            progression=progression or book_progression(),source_ruleset=ruleset or 'dnd2014',source_ruleset_version=1,
            reviewed=True,world=ScenarioPackage(name=name+' — начало',source='verified-entry',
                locations=locations,nodes=nodes,routes=routes,events=events),
            entry_stage=stage)
    except ValueError:
        return None

def entry_result(library,documents,result,ruleset,module):
    """Validate only the pinned entry closure; keep the full compile private."""
    # r250: проверенная сцена обязана дойти до мира текстом и первым фактом —
    # иначе мастеру нечем её описать и любой его ход отбрасывается. Починка
    # идёт до проверки `ready`, потому что собранный мир тоже может остаться
    # с пустым `facts` (описание ему уже поставил compile).
    page_text=entry_page_text(library,result)
    result=publish_entry_scene(result,page_text=page_text)
    if result['ready']:return result
    result=copy.deepcopy(result)
    readiness=dict(ready=False,scope='entry',reason='Открытая стартовая сцена и её обязательные зависимости ещё не подтверждены. Продолжить можно после проверки конкретной сцены; исходники и блоки сохранены.',quarantined=len(result['quarantine']))
    result['entry_readiness']=readiness
    # Any book can open a playable start, not only the pinned one. The check asks the
    # compiled world a question that does not depend on which book it is: is there a
    # confirmed opening place with reviewed text, and is it reachable from a start the
    # source itself named? A book that fails stays closed with the same explained
    # reason, and a book that passes is opened by its own facts, not by its hash.
    # A verified opening can carry a book whose full review failed only on what the
    # pages do not print. It cannot carry a broken claim: an NPC bound to a place
    # that does not exist, an unresolved dependency or an unconfirmed asset is a
    # defect of the same world, so the entry stays closed until it is repaired.
    # Вход публикует одну проверенную сцену: место попадает в мир только после
    # собственной проверки, поэтому чужая беда (несведённая награда, событие с
    # неполем) вход не запирает — она остаётся в счёте quarantined. Запирает
    # только дыра источника: страницы без текста.
    blocked=[q for q in result.get('quarantine') or [] if q.get('essential') and q.get('kind')=='source']
    opening=None if blocked else generic_entry(result,page_text=entry_page_text(library,result))
    if opening:
        name=opening['name'];definition=generic_definition(opening,result,ruleset,module)
        if definition is not None:
            result['full_definition']=result['definition'];result['full_ready']=False
            result.update(definition=definition.model_dump(by_alias=True),ready=True,start=name,title=definition.title)
            readiness.update(ready=True,reason='Готово начало: '+name+'. Доступны осмотр, лист героя и сохранение. Дальнейшие сцены остаются закрытыми, пока не проверены.',
                locations=len(definition.world.locations),npc=sum(len(p.npc) for p in definition.world.locations),
                transitions=len(definition.world.routes),entry_stage=definition.entry_stage.model_dump(by_alias=True) if definition.entry_stage else None,
                opening_source=dict(document=opening.get('document') or '',page=opening.get('page'),
                    text='printed-observable' if opening.get('published') else ('read-aloud' if not is_stub_description(name,opening.get('description')) else 'stub'),
                    sentences=len(opening.get('sentences') or [])))
            return result
    if HOARD not in documents:return result
    # The entry scene itself is edition-neutral: it is a place, a read-aloud box and an
    # observation boundary. Only Nat20's mechanics need a specific edition, and that is
    # chosen separately when the hero is created, so a source-edition mismatch must not
    # block the world. It is reported, and the hero launch still enforces mechanics.
    from .adventure_library import digest
    blob=library.blob(HOARD)
    if not blob.is_file() or digest(blob)!=HOARD:
        readiness['reason']='Исходный PDF отсутствует или его SHA-256 изменился.';return result
    with library.db() as db:
        rows={r['number']:r['content'] for r in db.execute('SELECT number,content FROM pages WHERE document=? AND number IN (7,8,10)',(HOARD,))}
    try:
        page=rows[8];opening=page[page.index('Уже несколько дней вы путешествуете'):page.rindex('Эпизод 1:')].strip()
        assert hashlib.sha256(opening.encode()).hexdigest()==OPENING_SHA
        assert 'персонажей 1го уровня' in rows[7] and 'партию из четырех персонажей' in rows[7]
        assert 'это столкновение долж' in rows[10] and 'предшествовать всем прочим' in rows[10] and 'восьмерыми кобольдами' in rows[10]
        # The place name must come from the verified page itself, not from whatever the
        # analysis happened to name: a correct read-aloud still starts the scene even when
        # the model's proposal is quarantined for some other, later chapter.
        assert 'Гринест' in opening
    except (KeyError,ValueError,AssertionError):
        readiness['reason']='Стартовый открытый блок (PDF, стр. 8), условия героя или обязательная граница (стр. 10) не совпали с проверенной версией источника.';return result
    # Verified on PDF physical page 8: the lower-right framed read-aloud box,
    # distinct ScalaSansCyrillic text, not the GM paragraph immediately above it.
    # The whole source hash AND the extracted public-text hash must match.
    name='Приближение к Гринесту'
    stage=dict(policy='verified-entry-v1',document=HOARD,page=8,opening_sha256=OPENING_SHA,boundary=BOUNDARY)
    # The book is 2014; the mechanics are whatever preset the player runs (Nat20 is
    # 2024). The entry scene is edition-neutral: a place, a read-aloud box and an
    # observation boundary, so it is prepared for the active preset and the book's own
    # edition is recorded separately.
    from .preparation_pipeline import active_mechanics
    from .progression import book_progression
    # The book advances by milestone (page 7), so the prepared start states that
    # model instead of leaving the player without any advancement rule.
    definition=AdventureDefinition(id=module,title='Гринест в огне — начало',ruleset=active_mechanics(),ruleset_version=1,progression=book_progression(),
        source_ruleset=ruleset or 'dnd2014',source_ruleset_version=1,reviewed=True,minimum_level=1,maximum_level=1,recommended_party=4,
        world=ScenarioPackage(name='Гринест в огне — начало',source='verified-entry',locations=[WorldLocation(name=name,description=opening,known_to_hero=True)],nodes=[WorldNode(id='entry',name=name,coordinates=[0,0])]),entry_stage=stage)
    result['full_definition']=result['definition'];result['full_ready']=False
    result.update(definition=definition.model_dump(by_alias=True),ready=True,start=name,title=definition.title)
    readiness.update(ready=True,reason='Готово начало на холме перед Гринестом. Доступны осмотр, лист героя и сохранение. Дальнейшие сцены изолированы.',locations=1,npc=0,transitions=0,boundary=BOUNDARY,source_pages=[7,8,10],rules=['dnd2014:level-1','observation:no-time-no-roll'],full_adventure_ready=False)
    return result
