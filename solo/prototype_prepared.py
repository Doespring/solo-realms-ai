"""Launch explicit Nat20 builds in the existing confirmed preparation/world data."""
from copy import deepcopy
import hashlib
import json

from .contracts import Strict, Hero
from .preparation import Draft
from .world import ScenarioPackage, WorldLocation, WorldNode, WorldSettings, import_package, observe


def _episode_one(library):
    """Build the reviewed closure; a non-matching book simply yields None."""
    from .first_episode import definition, verify_anchors
    try:
        verify_anchors(library)
        return definition(library,'dnd2024',1)
    except Exception:
        return None



def _road(route, anchor):
    """One road, re-anchored to the node its place already has."""
    field = "from_"
    start = getattr(route, field)
    return route.model_copy(update={field: anchor.get(start, start),
                               "to": anchor.get(route.to, route.to)})


def verified_episode_one(documents):
    """True only for the pinned book this closure was reviewed against."""
    from .first_episode import HOARD
    return HOARD in set(documents or ())


def episode_one_definition(library,ruleset_id,ruleset_version):
    """Build the reviewed closure, or None when this book was not verified."""
    from .first_episode import definition, verify_anchors
    verify_anchors(library)
    return definition(library,ruleset_id,ruleset_version)


def episode_one_world(definition):
    """The closure's places and routes, ready to merge into the launch world."""
    return definition.world.model_copy(deep=True)


def _progression(episode_ready, prepared_result=None):
    """The advancement model the prepared book carries, or its own default.

    A book states how its characters advance - milestones, priced experience or
    nothing at all - and that answer is part of the preparation. It must reach
    the saved campaign, otherwise a book that pays experience points loses them
    and every scenario silently becomes the default model.
    """
    from .progression import UNSET, Progression, book_progression
    candidates=[getattr(episode_ready, 'progression', None),
        ((prepared_result or {}).get('definition') or {}).get('progression')]
    for candidate in candidates:
        if isinstance(candidate,dict):
            try:candidate=Progression.model_validate(candidate)
            except ValueError:candidate=None
        if candidate is not None and getattr(candidate, 'mode', UNSET) != UNSET:
            return candidate
    return book_progression()


def _episode_two(library):
    """Build the reviewed continuation, or None when the pages do not match."""
    from .second_episode import definition, verify_anchors
    try:
        verify_anchors(library)
        return definition(library,'dnd2024',1)
    except Exception:
        return None


def verified_episode_two(documents):
    from .first_episode import HOARD
    return HOARD in set(documents or ())


def episode_two_definition(library,ruleset_id,ruleset_version):
    from .second_episode import definition, verify_anchors
    verify_anchors(library)
    return definition(library,ruleset_id,ruleset_version)


def episode_two_world(definition):
    return definition.world.model_copy(deep=True)


def episode_two_encounters():
    from .second_episode import encounters
    return encounters()


def default_encounters():
    """One verified encounter so a table can actually fight.

    The engine resolves attacks, damage and initiative, but preparation passed
    encounters={} and the note said they were unconfirmed, so no player ever
    reached a fight. The monster comes from the engine's own dataset, not from
    an invented stat block.
    """
    from .nat20_prototype import PrototypeEncounter
    return {'first-blood': PrototypeEncounter.model_validate(dict(
        grid=dict(width=12, height=10, blocked_cells=[]),
        hero_position=dict(x=1, y=1, z=0),
        # 'goblin' is not a monster id in the dataset; goblin-warrior is.
        actors=[dict(id='goblin-1', monster='goblin-warrior', position=dict(x=7, y=5, z=0), xp_value=50)],
    ))}


def mechanics_report():
    """The mapping of source mechanics onto Nat20, with every replacement stated."""
    from .mechanics_mapping import adapt, build, resolve_check, resolve_monster
    return build(
        source_ruleset='dnd2014', source_version=1,
        mechanics_ruleset='dnd2024', mechanics_version=1,
        substitutions=[
            resolve_monster('кобольд',page=10),
            resolve_monster('культист',page=17),
        ],
        adaptations=[
            adapt('rule','опыт за побеждённых врагов',
                  'В закреплённом корпусе нет таблицы опыта по уровню опасности, поэтому опыт за монстров равен нулю и не придумывается.',page=18),
            adapt('rule','опыт за спасённых горожан (по 50 за НИП)',
                  'Начисление опыта за неигровых персонажей требует отдельного подтверждённого счётчика.',page=10),
            adapt('item','лагерь: снаряжение культистов как маскировка',
                  'Скрытность под видом культиста — социальная процедура, а не зарегистрированная операция Nat20.',page=18),
        ],
    )


def native_start_blockers(definition,start):
    """A reviewed legacy procedure is not automatically executable by Nat20."""
    blockers=[]
    if definition.get('start_conditions'):
        blockers.append('Условия старта источника ещё не привязаны к выбранному Nat20-листу.')
    for event in definition.get('events',[]):
        if event['category']!='mandatory' or event.get('action_kind') or event.get('after_minutes',0)>0:continue
        elsewhere=any(c['path']=='location' and c.get('op','eq')=='eq' and c.get('value')!=start for c in event.get('conditions',[]))
        if not elsewhere:
            blockers.append('Обязательная процедура старта '+event['id']+' не подтверждена для Nat20; исходные эффекты не перенесены.')
    return blockers


def prepared_start_readiness(preparation, draft_id):
    """Safe local projection; never promotes a partial/name-only source result."""
    draft=preparation.get(draft_id)
    pipeline=getattr(preparation,'preparation_pipeline',None)
    blocks=dict(blocks_done=0,blocks_total=0)
    try:
        if pipeline is None:raise ValueError('Сервис структурной подготовки недоступен.')
        # Числа главы нужны и экрану, и отказу входа: «96 из 96» — это факт,
        # который игрок видит рядом с кнопкой, а не только в журнале.
        try:latest=pipeline.latest_run(draft_id);blocks=dict(blocks_done=int(latest['done']),blocks_total=int(latest['total']))
        except (ValueError,KeyError):pass
        _,run_id,result=pipeline.world_result(draft_id)
    except ValueError as exc:
        return dict(scope='world',ready=False,world_ready=False,partial_usable=False,canon_ready=False,
                    revision=draft['revision'],reasons=[str(exc)],blocked_operations=[],**blocks)
    definition=result['definition']
    blocked=[dict(key=q.get('key'),kind=q['kind'],reason=q['reason']) for q in result['quarantine']]
    blocked += [dict(kind=key,reason='Исходная механика не переносится в Nat20 автоматически.')
                for key in ('events','tables','trackers','survival','companions','wilderness') if definition.get(key)]
    token=draft['body'].get('world_confirmation',{})
    # The book's edition and the mechanics edition are two facts: report each from its
    # own field instead of reading the source edition out of the mechanics preset.
    # The book states the level it is written for. The hero wizard opens at that
    # level instead of always at 1, because a 5th-level adventure cannot be
    # entered by a 1st-level hero and silently lowering the book is not allowed.
    # A book that prints no recommendation reports null and the hero starts at 1.
    # The range (not only its lower end) travels too: the editor offers the
    # campaign's own levels and names the book that asked for them.
    level=definition.get('minimum_level')
    # Чьё это требование — тоже факт книги, а не догадка экрана: редактор пишет
    # словами «книга рассчитана на 8-й уровень», и имя книги берётся здесь же.
    book=str(draft['body'].get('name') or '')
    confirmed=token.get('run_id')==run_id
    # Два разных вопроса больше не сливаются в один: «мир можно использовать»
    # (глава собрана и её старт проверен) и «мир прошёл канон» (собран целиком,
    # а не только вход). Канон не ослабляется: `canon_ready` — та же проверка
    # полного мира, что и раньше, просто названная отдельно.
    canon=bool(result.get('full_ready',result.get('ready')))
    partial_usable=not canon
    if confirmed:reasons=[]
    elif partial_usable:reasons=['Глава собрана, часть деталей мира не проверена. Можно начать — детали догрузим позже.']
    else:reasons=['Подтвердите проверенный мир отдельно.']
    return dict(scope='world',ready=confirmed,world_ready=True,partial_usable=partial_usable,canon_ready=canon,run_id=run_id,revision=draft['revision'],
                minimum_level=definition.get('minimum_level'),maximum_level=definition.get('maximum_level'),
                recommended_party=definition.get('recommended_party'),hero_level=level,
                book=book,
                source_ruleset=definition.get('source_ruleset') or definition['ruleset'],
                mechanics_ruleset=definition['ruleset'],
                reasons=reasons,**blocks,
                blocked_operations=blocked,blocked_groups=_blocked_groups(blocked))


def launch_prepared(preparation, draft_id, *, confirm, expected_revision, build, encounters=None,
                    checks=None, prepared_spells=(), seed=None, mechanics_ruleset=None, rules_confirmed=False):
    from .nat20_api import Nat20GameEngine
    if not confirm:
        raise ValueError('Подтвердите подготовленный мир и выбранный Nat20-лист.')
    draft = preparation.get(draft_id)
    body = Draft.model_validate(draft['body'])
    if mechanics_ruleset not in (None,'dnd2024'):
        raise ValueError('Nat20 поддерживает явно выбранную механику dnd2024.')
    if draft['campaign']:
        current=preparation.store.campaign(draft['campaign'])
        if 'nat20' not in current['state']:raise ValueError('Черновик связан с legacy-кампанией; она не переводится в Nat20.')
        requested=Nat20GameEngine.preview_build(build,prepared_spells)['build']
        definition=current['state']['nat20']['definition']
        if definition.get('build')!=requested or set(definition.get('prepared_spells',[]))!=set(prepared_spells):
            raise ValueError('Этот старт уже связан с другим Nat20-листом или набором заклинаний.')
        return dict(campaign=current, limitations=current['state'].get('preparation',{}).get('blocked_operations',[]), replayed=True)
    if draft['revision'] != expected_revision:
        raise ValueError('Подготовка изменилась. Обновите выбранный старт.')
    if not body.prepared or not body.background_confirmed:
        raise ValueError('Нужны подтверждённая подготовка и предыстория.')
    # A world whose source never named its opening place is prepared, but not yet
    # placed. Blocking here would close the only door to the question: the place
    # is agreed in dialogue, and dialogue needs the campaign to exist. So an
    # unnamed opening is allowed to open, and the Master asks there instead.
    if body.start_name.strip() and not body.opening.strip():
        raise ValueError('Нужна открытая стартовая сцена: у согласованного места нет описания.')
    if body.ruleset != 'dnd2024' and (mechanics_ruleset!='dnd2024' or not rules_confirmed):
        raise ValueError('Источник/черновик использует '+body.ruleset+'. Явно выберите и подтвердите механику dnd2024; исходная редакция сохранится.')
    prepared_result=None
    if body.flow=='simple':
        pipeline=getattr(preparation,'preparation_pipeline',None)
        token=body.world_confirmation
        if pipeline is None or not token.get('run_id'):raise ValueError('Сначала подтвердите проверенную структуру мира; списка имён недостаточно.')
        _,_,prepared_result=pipeline.world_result(draft_id,token['run_id'])
        expected_hash=hashlib.sha256(json.dumps(prepared_result['definition'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if token.get('definition_sha256')!=expected_hash:raise ValueError('Подтверждённая структура мира изменилась.')
        start=prepared_result.get('start') or ''
        opening=next((p['description'] for p in (prepared_result['definition'].get('world') or {}).get('locations') or []
                      if start and p['name']==start),'')
        if body.start_name!=start or body.opening!=opening:
            raise ValueError('Стартовая сцена отличается от проверенной. Подтвердите актуальный мир.')
    if any(s['quality']['status'] != 'usable' for s in draft['sources']):
        raise ValueError('Есть непрочитанный источник. Подготовьте читаемые материалы или отдельную песочницу.')
    api = Nat20GameEngine(preparation.store)
    preview = api.preview_build(build, prepared_spells)
    selected = preview['build']
    authored_hero = body.hero
    if authored_hero.get('class_name') and authored_hero['class_name'].casefold() not in selected['classes']:
        raise ValueError('Класс подтверждённого героя отличается от выбранного Nat20-листа. Согласуйте выбор.')
    if authored_hero.get('species') and authored_hero['species'].casefold() != selected['species_slug']:
        raise ValueError('Вид подтверждённого героя отличается от Nat20-листа. Согласуйте выбор.')
    packages, limitations = [], []
    episode_ready = None
    episode_two_ready = None
    with preparation.connection() as db:
        packages = [ScenarioPackage.model_validate_json(row[0]) for row in db.execute(
            'SELECT package FROM sources WHERE draft=? AND active=1 AND package IS NOT NULL', (draft_id,))]
    if body.adventure_bundle:
        from .adventure_library import AdventureLibrary
        definition = AdventureLibrary(preparation.store.path.parent).definition(body.adventure_bundle, body.adventure_module or None)
        # Сырой результат прогона хранит None в незаполненных полях, а
        # model_dump подставляет значения по умолчанию, поэтому сравнивать
        # их напрямую нельзя: валидный мир объявлялся бы изменившимся
        # (progression.mode, source_ruleset, sealed_text и др.).
        _model = type(definition)
        _confirmed = _model.model_validate(prepared_result['definition']).model_dump(by_alias=True)
        if prepared_result and definition.model_dump(by_alias=True) != _confirmed:
            raise ValueError('Опубликованный мир отличается от подтверждённой версии подготовки.')
        if not definition.reviewed or definition.world is None:
            raise ValueError('У приключения нет проверенного подготовленного мира. Один PDF не задаёт игровой старт.')
        # A verified entry stage is a real start: the read-aloud place plus an explicit
        # boundary. It is not the whole book, so the entry guard keeps every action except
        # observation closed and states why. Refusing the launch instead would strand a
        # player who paid for a correct preparation with no way to enter it.
        if definition.entry_stage:
            limitations.append('Открыт проверенный вход источника; остальные сцены закрыты до отдельной проверки. '+definition.entry_stage.boundary)
        blockers=native_start_blockers(definition.model_dump(),body.start_name)
        if blockers:raise ValueError('; '.join(blockers))
        packages.append(definition.world)
        if any(getattr(definition,k) for k in ('events','tables','trackers','survival','companions','wilderness','unsupported')):
            limitations.append('Подключён подготовленный мир; сценарные таблицы, трекеры и неподдержанные эффекты не перенесены в Nat20 автоматически.')
        # With no further scene the entry place has no exit, so the verified
        # closure of episode one is what makes the first scene playable.
        from .first_playable import HOARD
        if HOARD in set((prepared_result or {}).get('start_contract',{}).get('documents',[])):
            from .adventure_library import AdventureLibrary
            from .adventure_engine import initialize
            episode = _episode_one(AdventureLibrary(preparation.store.path.parent))
            if episode is not None:
                packages.append(episode.world)
                episode_ready = episode
                limitations.append('Открыт проверенный первый эпизод: улица Гринеста, дорога к крепости и первое '
                    'столкновение восьмерых кобольдов (PDF, стр. 10-11). Остальные эпизоды закрыты до отдельной проверки.')
                # Episode two is what happens after the keep gate, so the player is not
                # stranded at the end of the first scene.
                second = _episode_two(AdventureLibrary(preparation.store.path.parent))
                if second is not None:
                    packages.append(second.world)
                    episode_two_ready = second
                    limitations.append('Открыт проверенный второй эпизод: тропа, застава отставших и лагерь '
                        'налетчиков; четыре культиста и восемь кобольдов у костра (PDF, стр. 16-18).')
                mapping = mechanics_report()
                limitations.append('Механика считается по правилам 2024 через Nat20. Книга написана для 2014: '
                    'исходная редакция сохранена отдельно, замены перечислены в отчёте сопоставления.')
                for note in mapping.adaptations:
                    limitations.append('Требует адаптации: '+note.source+' — '+note.needs)
    if prepared_result:
        limitations += [q['kind']+': '+q['reason'] for q in prepared_result['quarantine']]
    if not encounters:
        encounters = default_encounters()
    if not encounters:
        limitations.append('Боевые встречи не подтверждены. start_combat требует отдельно проверенного профиля встречи; проверки и другие зарегистрированные действия доступны.')
    if body.source_kind in ('ready','custom') and not packages:
        raise ValueError('Из источника получен текст, но нет подготовленной структуры мира. Подтвердите подготовку или выберите песочницу.')
    if not packages:
        packages = [ScenarioPackage(name=body.name,source='Confirmed preparation:'+draft_id,
                    locations=[WorldLocation(name=body.start_name,description=body.opening,known_to_hero=True)],
                    nodes=[WorldNode(id='start',name=body.start_name,coordinates=[1,1])],routes=[],events=[])]
    # Existing resolver validates references/IDs and assigns canonical NPC identities.
    world = dict(location=body.start_name,turn=0,locations=[],facts=[],deferred=[],
                 geography=dict(nodes=[],routes=[]),
                 # A prepared book that prints several places and the roads between them
                 # already carries a map. Turning the map layer on at launch lets the
                 # party actually use those roads from the screen instead of being shown
                 # a single starting scene with no way out.
                 world=dict(settings=WorldSettings(mode=body.world_mode,
                     map_travel=bool(packages and any(p.routes for p in packages))).model_dump()))
    taken = []
    for package in packages:
        package = package.model_copy(deep=True)
        for place in package.locations:
            if place.name == body.start_name:
                place.description = body.opening
                place.known_to_hero = True
            elif not place.secrets:
                # A place whose prepared record holds no secret is already public
                # material: the book prints it, so the hero knows where it is. Leaving
                # these unknown closed the only road out of the opening scene, because
                # travel refuses a destination the hero has never heard of and a place
                # becomes known only by arriving there. Secrets stay hidden: a place
                # with private content is learned by playing, not at launch.
                place.known_to_hero = True
                # known() also requires the map node to agree: an unknown node keeps a
                # known location unreachable, so the two flags move together.
                for node in package.nodes:
                    if node.name == place.name:
                        node.known = True
            for person in place.npc:
                person.private_knowledge = [*person.private_knowledge, *([person.known] if person.known else [])][:20]
                person.known = ''
        # A book prepared once and then closed into a reviewed episode names its
        # opening scene twice: once in the published world and once in the episode.
        # import_package refuses a place it already has, and the map refuses a
        # second node for one room, so the repeated place is taken once. Nothing
        # is lost: the place already in the world keeps its own description, and
        # the other package contributes only what it alone names. A place that
        # carries private content is never treated as a repeat, because that
        # content would disappear silently.
        #
        # import_package writes the places itself, so the world is still empty
        # here and the comparison is against the packages already taken.
        known = {loc.name for pkg in taken for loc in pkg.locations}
        # The node that already stands for a repeated place belongs to the
        # package that introduced it, not to this one.
        standing = {node.name: node.id for pkg in taken for node in pkg.nodes}
        if any(loc.name in known and (loc.npc or loc.interactions or loc.secrets)
               for loc in package.locations):
            raise ValueError('Повторяющаяся локация несёт собственные данные.')
        # Keep the ids of this package's routes identical to their places: a node
        # dropped with a repeated place is replaced by the node that place
        # already has, so every road stays anchored to a place that exists.
        # The key is the NODE id a road names, not the place name: a road
        # says "approach -> street", so the anchor maps the dropped node id
        # to the node the repeated place already has.
        dropped = {node.id: standing[node.name] for node in package.nodes
                   if node.name in known and node.name in standing}
        if dropped:
            package = package.model_copy(update=dict(
                locations=[loc for loc in package.locations if loc.name not in known],
                nodes=[node for node in package.nodes if node.name not in known],
                routes=[_road(route, dropped) for route in package.routes]))
            # A road that became a loop (the repeated place is both ends) is not a
            # road, and the map rejects it.
            package = package.model_copy(update=dict(
                routes=[route for route in package.routes if route.from_ != route.to]))
        import_package(world, package)
        taken.append(package)
    # An unnamed opening is not an error: the material never printed the starting
    # place, so the Master asks for it in the first game turn. Requiring it here
    # would demand the answer before the conversation that produces it.
    if body.start_name.strip() and not any(p['name'] == body.start_name for p in world['locations']):
        raise ValueError('Подтверждённая стартовая локация отсутствует в подготовленном мире.')
    observe(world)
    # Recover the existing cross-database launch protocol without creating a second campaign.
    with preparation.store.connection() as db:
        old = db.execute("SELECT id FROM campaigns WHERE json_extract(state,'$.preparation.draft')=?", (draft_id,)).fetchone()
    if old:
        current = preparation.store.campaign(old['id'])
        if current['state'].get('nat20',{}).get('definition',{}).get('build') != selected:
            raise ValueError('Этот подготовленный старт уже связан с другим листом.')
    else:
        current = api.create_experimental_campaign(authored_hero.get('name') or body.name,build=selected,
            encounters=encounters or {},checks=checks,prepared_spells=prepared_spells,seed=seed,
            # The opening place may still be unnamed; the campaign then keeps
            # the adventure's own name instead of an empty title.
            title=body.start_name or draft['body'].get('name') or body.name,
            description=(body.opening or draft['body'].get('description') or '')[:1500],
            # The advancement model is the scenario's, so it is decided at
            # preparation time and carried into the saved campaign.
            progression=_progression(episode_ready,prepared_result),episode_id=getattr(episode_ready,'id',None))
        # Уровень, на который рассчитана книга: из подготовленного мира, а не из
        # одного эпизода — эпизод книги для 1..20 уровней не описывает всю книгу.
        levels = (prepared_result or {}).get('definition') or {}
        loaded = locals().get('definition')
        book_minimum = levels.get('minimum_level') or getattr(loaded, 'minimum_level', None)
        book_maximum = levels.get('maximum_level') or getattr(loaded, 'maximum_level', None)
        book_party = levels.get('recommended_party') or getattr(loaded, 'recommended_party', None)
        book_name = str(draft['body'].get('name') or (levels.get('title') if levels else '') or '')

        def install(state):
            for key in ('location','locations','geography','world'):
                state[key] = deepcopy(world[key])
            # Player-facing state, logs and memory contracts all require a scene
            # name. Until the table names the opening place, the scene says so
            # instead of borrowing geography the material never printed.
            if not str(state.get('location') or '').strip():
                state['location'] = 'Место начала ещё не названо'
            if episode_ready is not None:
                from .adventure_engine import initialize
                initialize(state,'hoard-episode-1',episode_ready)
                # Register the continuation as a prepared volume so the campaign can
                # move on after the keep gate without a second preparation run.
                state.setdefault('adventure',{})['prepared_volumes'] = ['hoard-episode-1']
                if episode_two_ready is not None:
                    from .adventure_library import AdventureLibrary
                    state['adventure']['prepared_volumes'].append('hoard-episode-2')
            state['preparation'] = dict(draft=draft_id,background_confirmed=True,source_count=len(draft['sources']),
                                        atmosphere=body.atmosphere,public_description=body.description[:500])
            state['preparation'].update(source_ruleset=body.ruleset,mechanics_ruleset='dnd2024',
                world_confirmation=body.world_confirmation,source_documents=(prepared_result or {}).get('start_contract',{}).get('documents',[]),
                blocked_operations=limitations)
            if prepared_result:state['preparation']['source_count']=len(state['preparation']['source_documents'])
            # What the material never said travels into the campaign as open
            # questions, so the Master asks them in dialogue instead of the
            # preparation refusing to open. A answered question is recorded and
            # never asked again, including after a restart.
            state['preparation']['open_questions']=list(body.questions or [])
            state['preparation']['answers']=dict(state['preparation'].get('answers') or {})
            # L02: требование книги — факт кампании, а не догадка экрана. По нему
            # Мастер на старте сверяет уровень героя с книгой и, если игрок
            # отказался пересобирать, ведёт опасность под его уровень. Пишется
            # здесь, а не выше: блок `preparation` собирается заново и стёр бы
            # поле, записанное до него. Переживает перезапуск — книгу повторно
            # читать не нужно.
            from . import level_gate
            level_gate.install_requirement(state, book=book_name,
                minimum=book_minimum, maximum=book_maximum, party=book_party)
            for field in ('background','personality','appearance','goals','bonds','ideals','alignment'):
                if field in authored_hero:state['hero'][field] = deepcopy(authored_hero[field])
            evidence = [dict(text=known,known_by=[person['name']],source_ref='preparation:'+draft_id,
                            provenance='scenario',truth='fact',scope='private',kind='npc_knowledge')
                        for place in state['locations'] for person in place.get('npc',[])
                        for known in person.get('private_knowledge',[])]
            if prepared_result:
                for item in evidence:
                    item['source_ref'] += ':run:'+body.world_confirmation['run_id']
            # The Master opens with what the material gave, then asks what it did
            # not give. None of these questions blocks the table.
            opening=body.opening
            pending=[entry.get('question','') for entry in (body.questions or []) if entry.get('question')]
            if pending:
                opening=opening.rstrip()+'\n\n'+pending[0]
            return [dict(type='scripted',kind='prepared_world',text=opening,memory_evidence=evidence,
                         open_questions=pending)]
        preparation.store.record_scenario(current['id'],'prepared-world',body.opening,install)
        current = preparation.store.campaign(current['id'])
    with preparation.connection() as db:
        db.execute('UPDATE drafts SET campaign=? WHERE id=? AND campaign IS NULL', (current['id'],draft_id))
    return dict(campaign=current,limitations=limitations + preview['warnings'],replayed=bool(old))


def bind_library_module(preparation,campaign,draft_id):
    """Give a campaign that starts without instructions the module its book was cut into.

    The pages are real: the module was cut from pages that were read, and the
    first of them becomes the opening scene, so the Master's first turn stands on
    that page instead of on an empty world. The instructions are not: an
    auto-split module holds no events, and the campaign says so instead of
    pretending the scenario runs.
    """
    draft=preparation.get(draft_id)
    if draft['campaign'] and draft['campaign']!=campaign:raise ValueError('Этот черновик уже начал другую кампанию.')
    body=Draft.model_validate(draft['body'])
    if not body.adventure_bundle:return None
    from .adventure_library import AdventureLibrary
    library=AdventureLibrary(preparation.store.path.parent)
    try:modules=library.modules(body.adventure_bundle)
    except KeyError:return dict(bound=False,notice='Сборник книги не найден в библиотеке: кампания начнётся без книги.')
    chosen=next((m for m in modules if m['id']==body.adventure_module and m['ready']),None) or next((m for m in modules if m['ready']),None)
    if chosen is None:
        reason=(modules[0]['notes'][0] if modules and modules[0]['notes'] else 'границы глав не найдены')
        return dict(bound=False,notice='Книга прочитана, но '+reason+': кампания начнётся без книги.')
    definition=library.definition(body.adventure_bundle,chosen['id'])
    ranges=[(s.document,s.first_page,s.last_page) for s in definition.source_ranges]
    if not ranges or not ranges[0][0] or not ranges[0][1]:
        return dict(bound=False,notice='Модуль книги не называет ни одной страницы: кампания начнётся без книги.')
    document,first,last=ranges[0]
    pages=[[a,b] for _,a,b in ranges]
    excerpt=(library.page_text(body.adventure_bundle,document,first) or '').strip() if first else ''
    title=(chosen['title'] or definition.title or 'Книга').strip()[:120]
    binding=dict(bundle=body.adventure_bundle,module=chosen['id'],title=title,document=document,pages=pages,page=first)
    # The chapter's own scene travels into the campaign: the place it names becomes
    # where the hero stands, with the people and the checks the pages printed. A
    # page range alone left the campaign in a scene named after the chapter, with
    # no place, no person and no check to act on.
    from .world import WorldLocation,WorldNode,import_package
    package=definition.world if definition and definition.world and definition.world.locations else None
    # Where the hero stands is not the first place the pages mention. «Эпизод 1:
    # Совет Глубоководья» prints «Башня Ксонтала» on its own pages, and a start in
    # the castle is not the episode the book named. The entry scene comes from the
    # parse of this module and keeps its confidence, so an unproven guess is not
    # dressed up as the book's own scene.
    entry=next((row.get('entry_scene') or {} for row in modules if row['id']==chosen['id']),{})
    ground=entry.get('place') or entry.get('name') or ''
    place=next((p for p in package.locations if p.name==ground),None) if package and ground else None
    # Названного места может не оказаться в мире: книгу исправили, место убрали
    # или страница его не напечатала. Тогда герой встаёт на первое место сцены —
    # это честная догадка, и метка входа уже сказала, что название не напечатано.
    if place is None:place=package.locations[0] if package else None
    # A03/F01: the master asked the player where the story starts and what the
    # opening says, and the player confirmed it. The book's first page fills that
    # scene in; it does not overrule the answer the launch was approved with.
    master_start=str(body.start_name or '').strip()
    master_opening=str(body.opening or '').strip()
    scene_name=master_start or (place.name if place else title)
    binding['place']=scene_name
    binding['scene']=dict(locations=len(package.locations) if package else 0,
                          npcs=sum(len(location.npc) for location in package.locations) if package else 0,
                          checks=sum(len(location.checks) for location in package.locations) if package else 0)
    # The opening place is named by the book, not by the uploader: the scene the
    # first turn stands in is the page the module starts at. An image page prints
    # no text, so the place keeps the module's own name instead of an empty scene.
    # A journal step holds at most 800 characters (solo/action_validator.py), so
    # the setup record carries the opening, not the whole page: a longer scene
    # stays in the place description, where the narrator reads it.
    # A04/F07: в сцену первого хода и в описание места идёт только открытая часть
    # страницы. Заголовок эпизода, обзор приключения и планы ведущего остаются в
    # книге (binding['source_text']): игрок их не читает и в статусе не видит.
    from .public_scene import public_scene_text
    opening=(master_opening or excerpt)
    shown=public_scene_text(opening,name=scene_name) or scene_name
    scene=shown[:780] or title
    binding['source_text']=opening[:2000]
    def install(state):
        state['location']=scene_name
        lands_here=WorldLocation(name=scene_name,description=shown[:2000] or title,known_to_hero=True,
                                 npc=[npc.model_copy(deep=True) for npc in (place.npc if place else [])],
                                 checks=dict(place.checks) if place else {},
                                 sealed_text=(place.sealed_text if place else '')[:3000])
        state['locations']=[lands_here.model_dump()]
        # The map keeps the node the scene itself gave this place, so the roads it
        # prints still find both ends. A second node invented for the same name made
        # import_package refuse the chapter («Локация уже имеет узел карты»), and the
        # campaign died on its own opening page before the hero could stand there.
        home=[node.model_copy(deep=True) for node in (package.nodes if package else []) if node.name==scene_name]
        state['geography']=dict(nodes=[node.model_dump() for node in home] or [WorldNode(id='start',name=scene_name,coordinates=[1,1]).model_dump()],routes=[])
        if package:
            rest=package.model_copy(deep=True)
            rest.locations=[location for location in rest.locations if location.name!=scene_name]
            rest.nodes=[node for node in rest.nodes if node.name!=scene_name]
            import_package(state,rest)
        prepared=dict(state.get('preparation') or {});prepared['library']=binding
        state['preparation']=prepared
        return [dict(type='scripted',kind='prepared_world',text=scene,source=binding)]
    preparation.store.record_scenario(campaign,'prepared-world',scene,install)
    with preparation.connection() as db:
        db.execute('UPDATE drafts SET campaign=? WHERE id=? AND campaign IS NULL',(campaign,draft_id))
    limitation='' if not binding['scene']['checks'] and not binding['scene']['npcs'] else ' Сцена из книги: место «'+scene_name+'», персонажей '+str(binding['scene']['npcs'])+', проверок '+str(binding['scene']['checks'])+' — каждая со своей страницей.'
    return dict(bound=True,notice='Книга привязана: «'+title+'», стр. '+str(first)+'–'+str(last)+'.'+limitation+' Сценарные инструкции книги ещё не проверены, поэтому кампания начинается на первой странице главы, без её событий.',**binding)


def register_api(app, store, game, *, preparation=None):
    from pydantic import Field
    class Launch(Strict):
        confirm: bool
        expected_revision: int = Field(ge=0)
        build: dict
        encounters: dict = Field(default_factory=dict)
        checks: dict | None = None
        prepared_spells: list[str] = Field(default_factory=list,max_length=100)
        seed: int | None = Field(default=None,ge=0,lt=2**63)
        mechanics_ruleset: str | None = None
        rules_confirmed: bool = False
    @app.post('/api/prototype/gm/prepared/{draft_id}/launch')
    async def launch(draft_id: str, body: Launch):
        service = preparation or getattr(game, 'preparation', None)
        if service is None:raise ValueError('Сервис подготовки не подключён.')
        async with game.operation:
            result = launch_prepared(service,draft_id,**body.model_dump())
            from .combat_view import public_campaign
            return dict(result,campaign=public_campaign(result['campaign']))
    @app.get('/api/prototype/gm/context/{campaign}')
    def context(campaign: str, query: str=''):
        return game.compiler.command_context(store.campaign(campaign),query=query[:4000])

#: Вид элемента по-русски: экран называет причину отказа, а не поле схемы.
_BLOCKED_KIND_LABELS={
    'location':'мест','npc':'персонажей','object':'объектов','event':'событий',
    'table':'таблиц','route':'переходов','opponent':'противников','reward':'наград',
    'special_rule':'особых правил','tracker':'счётчиков','chapter':'глав','scene':'сцен',
    'companion':'спутников','metadata':'ограничений приключения','coverage':'пометок анализатора',
    'source':'исходников','dependencies':'зависимостей','playable':'деталей мира',
    'locations':'локаций','ruleset':'редакции правил','start':'старта','media':'медиа',
}


def _blocked_groups(blocked,limit=8):
    """Почему забраковано — по причинам, а не стеной одинаковых строк.

    На большой книге `blocked_operations` — это сотни записей, и на экране они
    сливаются в один абзац: игрок не видит, что именно не сошлось. Здесь те же
    записи сведены по виду элемента и причине; ни одна не спрятана, полный
    список остаётся в `blocked_operations`. Хвост причины (имя места, перечень
    полей) отличает записи, но не причину, поэтому длинный хвост после
    последнего двоеточия в заголовок группы не идёт.
    """
    groups={}
    for row in blocked or []:
        kind=str(row.get('kind') or '')
        line=' '.join(str(row.get('reason') or '').split())
        head,detail=line,''
        # Ошибки схемы перечисляются через '; ' ('id: Field required; category:
        # Field required'): их текст и есть причина, дробить его нельзя. Всё
        # остальное после последнего двоеточия — значение (имя места, перечень
        # полей, кортеж концов маршрута), и оно группу не создаёт.
        if '; ' not in line and ':' in line:
            before,after=line.rsplit(':',1)
            if line.count(':')>=2 or after.strip().startswith('(') or len(after.strip())>60:
                head,detail=before.strip(),after.strip()
        if not head:head='Элемент не прошёл проверку'
        entry=groups.get((kind,head))
        if entry is None:
            entry=groups[(kind,head)]=dict(kind=kind,label=_BLOCKED_KIND_LABELS.get(kind,kind or 'элементов'),
                reason=head,count=0,detail=detail)
        entry['count']+=1
    ordered=sorted(groups.values(),key=lambda row:(-row['count'],row['kind'],row['reason']))
    return dict(total=len(blocked or []),groups=len(ordered),
        shown=ordered[:limit],hidden=max(0,len(ordered)-limit))

