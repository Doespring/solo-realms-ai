"""Source-pinned entry stages; a reviewed entry never approves the rest of a book.

Layout policies are deliberately document-specific. Typography alone is not proof
that an arbitrary box is player-facing. Unknown layouts remain closed.
"""
import copy,hashlib,re
from .adventure_contracts import AdventureDefinition
from .world import WorldEvent,WorldLocation,WorldNode,WorldRoute,ScenarioPackage

HOARD='7ad6e955bc095a88c1d57fa68f4f36ff714edbeeb31f362ac59db2ce778adbdf'
OPENING_SHA='c4b7ad1d949faf98fb5b64ee645a97ac99dd9eab480e69930ecb5098294b3111'
BOUNDARY='Переход в Гринест пока закрыт: не проверены боевые профили участников и процедура обязательного первого столкновения (PDF, стр. 10). Можно осмотреть видимое с холма, открыть лист героя или сохранить игру; время сцены не продвигается.'

def stage_of(state):return state.get('adventure',{}).get('definition',{}).get('entry_stage')

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

def generic_entry(result):
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
    if not start or len(places)<2:return None
    node=next((p for p in places if p.get('name')==start),None)
    if node is None:return None
    description=(node.get('description') or '').strip()
    if not description:return None
    if not (world.get('routes') or []):return None
    proof=_cited(result,start)
    return dict(name=start,description=description,page=proof.get('page') or 1,
        document=proof.get('document') or '',evidence=[proof] if proof else [],world=world)


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
        if here!=want:continue
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
    blocked=[q for q in result.get('quarantine') or [] if q.get('essential')]
    opening=None if blocked else generic_entry(result)
    if opening:
        name=opening['name'];definition=generic_definition(opening,result,ruleset,module)
        if definition is not None:
            result['full_definition']=result['definition'];result['full_ready']=False
            result.update(definition=definition.model_dump(by_alias=True),ready=True,start=name,title=definition.title)
            readiness.update(ready=True,reason='Готово начало: '+name+'. Доступны осмотр, лист героя и сохранение. Дальнейшие сцены остаются закрытыми, пока не проверены.',
                locations=len(definition.world.locations),npc=sum(len(p.npc) for p in definition.world.locations),
                transitions=len(definition.world.routes),entry_stage=definition.entry_stage.model_dump(by_alias=True) if definition.entry_stage else None)
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
