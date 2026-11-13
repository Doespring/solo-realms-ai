"""World commands share the existing journal and travel resolver."""
import copy,json,re,sqlite3,uuid
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from typing import Literal
from pydantic import Field
from .contracts import Strict
from .world import WorldSettings,ScenarioPackage,ensure,observe,import_package,project,settings,known,direction
from .rulesets import digest
from . import travel

class WorldCommand(Strict):
    request_id: str=Field(min_length=1,max_length=80)
    expected_version: int | None=Field(default=None,ge=0)
    kind: Literal['settings','travel','explore','marker','delete_marker','decline_encounter']
    target: str=Field(default='',max_length=120)
    direction: Literal['север','юг','восток','запад'] | None=None
    coordinates: list[int]=Field(default_factory=list,max_length=2)
    text: str=Field(default='',max_length=200)
    marker: str=Field(default='',max_length=80)
    exit: str=Field(default='',pattern=r'^\d{0,3}$')
    settings: WorldSettings | None=None
    confirm: bool=False
class WorldImport(Strict):
    request_id: str=Field(min_length=1,max_length=80)
    expected_version: int | None=Field(default=None,ge=0)
    confirm: bool=False
    package: ScenarioPackage

def travel_guard(state):
    from .contracts import Hero
    from .rulesets import RulesetEngine,active
    from .inventory import InventoryEngine
    hero=Hero.model_validate(state['hero'])
    # The mechanical backend is Nat20, and it keeps the live fight in
    # state['nat20'], not in the legacy state['combat'] map. Reading only the
    # legacy key let a hero walk away mid-fight at full speed. Ask the backend
    # that actually runs the combat; the legacy key stays readable for old saves.
    active_combat=False
    if state.get('combat',{}).get('active'):active_combat=True
    elif state.get('nat20'):
        # _bound reads the campaign envelope, not the bare state, and it is the
        # only reader that knows a Nat20 preset is pinned to dnd2024. When it
        # cannot answer, the guard must not silently assume "no fight": a
        # swallowed error here is what let the hero walk out of the encounter.
        from .nat20_api import Nat20GameEngine
        active_combat=bool(Nat20GameEngine._bound({'id':'travel-guard','state':state}).get('combat'))
    if active_combat:raise ValueError('Завершите бой прежде чем путешествовать')
    if state.get('hero_death') or hero.hp==0 or set(hero.conditions)&{'incapacitated','unconscious','paralyzed','stunned','petrified','grappled','restrained'}:raise ValueError('Герой сейчас не может идти')
    speed=RulesetEngine(active(state)).exhaustion_effects(hero)['speed']
    if state.get('economy'):
        inventory=InventoryEngine(state)
        if inventory.weight()>inventory.capacity():raise ValueError('Поклажа превышает переносимый вес')
        speed=max(0,speed-inventory.speed_penalty())
    if not speed:raise ValueError('Движение недоступно по текущим правилам')
    return speed

class WorldService:
    def __init__(self,store):self.store=store
    def preview(self,campaign,target):
        current=self.store.campaign(campaign);state=current['state'];speed=travel_guard(state)
        if not known(state,target):raise ValueError('Это место пока неизвестно герою')
        path,_=travel.itinerary(state,target)
        return dict(target=target,meters=sum(r['meters'] for r in path),minutes=sum(travel.duration(r,speed,state) for r in path),route=[r['description'] for r in path],version=current['version'],notice='Это план пешего пути. Обнаруженное препятствие или событие может остановить путешествие.')
    def execute(self,campaign,body,*,original=None,from_map=False):
        with self.store.connection(True) as c:
            current=self.store.campaign(campaign,c)
            def transition(state):
                from .first_playable import require_full_scene
                if body.kind not in ('settings','marker'):require_full_scene(state)
                if body.expected_version is not None and body.expected_version!=current['version']:raise ValueError('Мир изменился; обновите намерение')
                world=ensure(state)
                if body.kind=='settings':
                    if not body.confirm or body.settings is None:raise ValueError('Подтвердите изменение режима и настроек мира')
                    world['settings']=body.settings.model_dump();text='Настройки мира изменены. Прежние события и география сохранены.'
                elif body.kind=='marker':
                    if len(body.coordinates)!=2 or any(not 0<=n<80 for n in body.coordinates) or not body.text.strip():raise ValueError('Укажите координаты и название своей метки')
                    if len(world['markers'])>=200:raise ValueError('Достигнут лимит меток')
                    world['markers'].append(dict(id=str(uuid.uuid4()),coordinates=body.coordinates,text=body.text));text='Личная метка добавлена. Она не создаёт локацию или дорогу.'
                elif body.kind=='delete_marker':
                    if not any(m['id']==body.marker for m in world['markers']):raise ValueError('Метка не найдена')
                    world['markers']=[m for m in world['markers'] if m['id']!=body.marker];text='Личная метка удалена.'
                elif body.kind=='decline_encounter':
                    if not world.get('pending_encounter'):raise ValueError('Нет предложения столкновения')
                    world['pending_encounter']=None;text='Предложение столкновения отклонено. Боевые участники и победа не создавались.'
                else:
                    travel_guard(state)
                    if from_map and not settings(state).map_travel:raise ValueError('Выбор путешествия на карте отключён. Включите его явно в настройках мира; текстовое путешествие доступно.')
                    if body.kind=='travel':
                        if not known(state,body.target):raise ValueError('Это место неизвестно герою')
                        result=travel.advance(state,body.target)
                    elif body.kind=='explore':
                        if settings(state).mode=='story':raise ValueError('Исследование неизвестной дороги доступно в песочнице или смешанном режиме')
                        graph=travel.geography(state);by_id={n['id']:n for n in graph['nodes']};options=[]
                        for r in graph['routes']:
                            if not r.get('known',True) or not r.get('explorable') or settings(state).mode not in r.get('modes',['story','sandbox','mixed']):continue
                            for start,end in [(r['from'],r['to']),*([(r['to'],r['from'])] if not r.get('one_way') else [])]:
                                if by_id[start]['name']==state['location'] and not known(state,by_id[end]['name']):
                                    options.append({**r,'from':start,'to':end})
                        if body.exit:
                            index=int(body.exit)
                            options=[options[index]] if index<len(options) else []
                        else:options=[r for r in options if direction(by_id[r['from']]['coordinates'],by_id[r['to']]['coordinates'])==body.direction]
                        if len(options)!=1:raise ValueError('В этом направлении нет одной однозначной доступной неисследованной дороги')
                        result=travel.advance_path(state,options,graph)
                    text=('Прибытие: '+state['location'] if result['arrived'] else result['text'])+f". Пройдено {result['meters']} м за {result['minutes']} минут."
                    return [dict(type='scripted',kind='world_travel',text=text,travel=result,memory_evidence=result.get('memory_evidence',[]))]
                observe(state);return [dict(type='scripted',kind='world',text=text)]
            return self.store.engine_event(c,current,body.request_id,original or 'Мир: '+body.kind,transition,digest(body.model_dump(exclude={'request_id'})))
    def import_world(self,campaign,body):
        if not body.confirm:raise ValueError('Подтвердите добавление структурированного сценария в канон')
        with self.store.connection(True) as c:
            current=self.store.campaign(campaign,c)
            def transition(state):
                if state.get('combat',{}).get('active'):raise ValueError('Завершите бой перед импортом географии')
                if body.expected_version is not None and body.expected_version!=current['version']:raise ValueError('Мир изменился')
                import_package(state,body.package)
                return [dict(type='scripted',kind='world_import',text='Подтверждено расширение мира: '+body.package.name+'. Существующие локации и история не перезаписаны.')]
            return self.store.engine_event(c,current,body.request_id,'Добавление структурированного сценария',transition,digest(body.model_dump(exclude={'request_id'})))

# --- r236: готовые миры ------------------------------------------------------
# Разобранный мир сохраняется файлом *.soloworld.json и поднимается на другой
# машине без единого вызова модели. Пакет несёт не книгу, а уже собранное
# состояние кампании: места, географию, наблюдения, героя, механику и настройки.
# История ходов, журнал и память в пакет не входят: это новая кампания в том же
# мире, а не копия прохождения.
WORLD_FORMAT='soloworld'
WORLD_FORMAT_VERSION=1
WORLD_SUFFIX='.soloworld.json'
WORLD_FOLDER='worlds'
WORLD_MAX_BYTES=24*1024*1024
WORLD_MAX_LOCATIONS=400
WORLD_MAX_ROUTES=800
WORLD_MAX_NPCS=4000
_EDITIONS={'dnd2014':'D&D 2014','dnd2024':'D&D 2024'}
_TRANSLIT={'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m',
    'н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'c','ч':'ch','ш':'sh','щ':'sch','ъ':'',
    'ы':'y','ь':'','э':'e','ю':'yu','я':'ya'}

class WorldPackageProblem(ValueError):
    """Файл мира не принят; текст отказа читает человек, а не разработчик."""

def world_folder(store):
    return Path(store.path).parent/WORLD_FOLDER

def short_name(value):
    text=re.sub(r'[^0-9a-zа-яё]+','-',str(value or '').casefold().strip())
    text=''.join(_TRANSLIT.get(ch,ch) for ch in text)
    text=re.sub(r'-{2,}','-',text).strip('-')[:40]
    return text or 'world'

def _reason(exc):
    """Одна строка причины: pydantic даёт десяток строк, человеку нужна суть."""
    return ' '.join(str(exc).split())[:300]

def parse_package(text):
    """Разобрать текст пакета. Битый файл даёт причину, а не голый JSONDecodeError."""
    if not isinstance(text,str) or not text.strip():raise WorldPackageProblem('Файл мира пуст: в нём нет JSON.')
    try:raw=json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorldPackageProblem('Файл мира повреждён: JSON не разбирается (строка '+str(exc.lineno)+', столбец '+str(exc.colno)+'). Скачайте пакет заново.') from None
    return raw

def safe_file(store,name):
    """Только файл внутри папки миров: имя с путём наружу не открывается."""
    folder=world_folder(store)
    if not isinstance(name,str) or not name.strip() or Path(name).name!=name:raise WorldPackageProblem('Недопустимое имя файла мира.')
    if not name.endswith(WORLD_SUFFIX):raise WorldPackageProblem('Файл мира должен называться *'+WORLD_SUFFIX+'.')
    path=folder/name
    if not path.resolve().is_relative_to(folder.resolve()):raise WorldPackageProblem('Недопустимое имя файла мира.')
    if not path.is_file():raise KeyError('Файл мира не найден: '+name)
    return path

def _read_package(path):
    try:size=path.stat().st_size
    except OSError as exc:raise WorldPackageProblem('Файл мира не читается: '+_reason(exc))
    if size>WORLD_MAX_BYTES:raise WorldPackageProblem('Файл мира больше '+str(WORLD_MAX_BYTES//1048576)+' МБ — это не пакет мира.')
    try:text=path.read_text(encoding='utf-8')
    except UnicodeDecodeError:raise WorldPackageProblem('Файл мира не читается как текст UTF-8. Выберите файл, сохранённый приложением (*'+WORLD_SUFFIX+').') from None
    return parse_package(text)

def system_label(package):
    """Система так, как её видит игрок: механика и, отдельно, редакция книги."""
    settings=package.get('settings') if isinstance(package.get('settings'),dict) else {}
    ruleset=settings.get('ruleset') if isinstance(settings.get('ruleset'),dict) else {}
    system=package.get('system') if isinstance(package.get('system'),dict) else {}
    mechanics=system.get('id') or ruleset.get('id') or 'dnd2024'
    # Короткая подпись из редакции: полное имя набора («D&D 2024 · часть SRD
    # 5.2.1») в списке библиотеки занимает строку и не говорит игроку ничего
    # нового. Редакция книги уходит отдельным хвостом ниже.
    label=_EDITIONS.get(mechanics) or system.get('name') or ruleset.get('name') or str(mechanics)
    book=(settings.get('preparation') or {}).get('source_ruleset')
    if book and book!=mechanics:label+=' · книга '+_EDITIONS.get(book,str(book))
    return label

def _book_info(store,draft):
    """Отпечаток исходной книги (creation_runs.fingerprint) и чем она была.

    Читается из подготовки только на чтение: пакет мира не меняет её базу. Без
    готового прогона поля остаются пустыми — пакет от этого не перестаёт быть
    миром, просто у него нет книги-источника (например, песочница).
    """
    info=dict(draft=draft or '',title='',documents=0,fingerprint='',book_fingerprint='',prepared_at=None,status='')
    path=Path(store.path).parent/'preparation.sqlite3'
    if not draft or not path.is_file():return info
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            columns={row[1] for row in db.execute('PRAGMA table_info(creation_runs)')}
            if not columns:return info
            extra=',book_fingerprint' if 'book_fingerprint' in columns else ''
            row=db.execute('SELECT fingerprint,status,created,result'+extra+' FROM creation_runs WHERE draft=? ORDER BY created DESC LIMIT 1',(draft,)).fetchone()
            if row is None:return info
            result={}
            try:result=json.loads(row['result']) if row['result'] else {}
            except ValueError:result={}
            info.update(fingerprint=row['fingerprint'],status=row['status'],prepared_at=row['created'],
                title=str(result.get('title') or ''),
                documents=len(((result.get('start_contract') or {}).get('documents')) or []))
            if 'book_fingerprint' in columns:info['book_fingerprint']=row['book_fingerprint'] or ''
    except (sqlite3.Error,OSError):
        # Подготовка может быть занята записью: пакет мира важнее, чем её отпечаток.
        return info
    return info

def export_package(store,campaign):
    """Снимок мира кампании в переносимый пакет. Книга в пакет не попадает."""
    from .world import ensure
    current=store.campaign(campaign);state=current['state']
    if not isinstance(state.get('nat20'),dict):
        raise ValueError('Эта кампания идёт без движка Nat20: готовым миром можно сохранить только кампанию Nat20. Материалы и черновик остаются на месте.')
    locations=copy.deepcopy(state.get('locations') or [])
    if not locations:raise ValueError('В кампании нет ни одного места — сохранять нечего.')
    # Мир — это состояние без героя и движка: они едут отдельным разделом, чтобы
    # пакет читался человеком, а импорт не собирал состояние по кускам и ничего
    # не терял (экономика, часы мира, шаги приключения остаются в мире).
    world=copy.deepcopy(state)
    world.pop('hero',None);world.pop('nat20',None);world.pop('ruleset',None)
    ensure(world)
    ruleset=copy.deepcopy(state.get('ruleset') or {})
    preparation=world.get('preparation') if isinstance(world.get('preparation'),dict) else {}
    public={k:copy.deepcopy(preparation[k]) for k in
        ('source_ruleset','mechanics_ruleset','source_count','atmosphere','public_description','open_questions','answers','blocked_operations')
        if preparation.get(k) is not None}
    counts=dict(locations=len(locations),routes=len((world.get('geography') or {}).get('routes') or []),
        npcs=sum(len(place.get('npc') or []) for place in locations))
    return dict(format=WORLD_FORMAT,format_version=WORLD_FORMAT_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        short_name=short_name(current['name']),name=current['name'],
        system=dict(id=ruleset.get('id') or 'dnd2024',name=ruleset.get('name') or 'D&D 2024',
            edition=ruleset.get('edition') or '2024',version=ruleset.get('version') or 1),
        book=_book_info(store,preparation.get('draft')),
        settings=dict(world=copy.deepcopy((world.get('world') or {}).get('settings') or {}),
            ruleset=ruleset,preparation=public),
        world=world,characters=dict(hero=copy.deepcopy(state.get('hero') or {}),engine=copy.deepcopy(state['nat20'])),
        counts=counts)

def validate_package(raw):
    """Строгая проверка пакета до записи в базу: версия, поля, размеры, ссылки."""
    if not isinstance(raw,dict):raise WorldPackageProblem('Файл мира должен быть JSON-объектом.')
    if raw.get('format')!=WORLD_FORMAT:
        raise WorldPackageProblem('Это не пакет мира Solo Realms: в файле нет отметки «'+WORLD_FORMAT+'». Мир сохраняется кнопкой «Сохранить мир в файл».')
    version=raw.get('format_version')
    if not isinstance(version,int) or isinstance(version,bool) or version<1:
        raise WorldPackageProblem('В пакете не указана версия формата: файл мог быть изменён вручную.')
    if version>WORLD_FORMAT_VERSION:
        raise WorldPackageProblem('Пакет создан более новой версией приложения (формат '+str(version)+', эта сборка понимает до '+str(WORLD_FORMAT_VERSION)+'). Обновите приложение.')
    world=raw.get('world')
    if not isinstance(world,dict):raise WorldPackageProblem('В пакете нет мира: поле world отсутствует или не объект.')
    locations=world.get('locations')
    if not isinstance(locations,list) or not locations:raise WorldPackageProblem('В пакете нет ни одного места: загружать нечего.')
    if len(locations)>WORLD_MAX_LOCATIONS:raise WorldPackageProblem('В пакете больше '+str(WORLD_MAX_LOCATIONS)+' мест — файл повреждён или это не мир Solo Realms.')
    names=[]
    for index,place in enumerate(locations):
        if not isinstance(place,dict):raise WorldPackageProblem('Место №'+str(index+1)+' в пакете не является объектом.')
        name=place.get('name')
        if not isinstance(name,str) or not name.strip():raise WorldPackageProblem('Место №'+str(index+1)+' без имени.')
        if len(name)>120:raise WorldPackageProblem('Имя места длиннее 120 знаков: файл повреждён.')
        names.append(name)
    if len(set(names))!=len(names):
        duplicates=sorted({name for name in names if names.count(name)>1})
        raise WorldPackageProblem('В пакете повторяются места: '+', '.join(duplicates[:3])+'.')
    geography=world.get('geography') or {}
    if not isinstance(geography,dict):raise WorldPackageProblem('География мира в пакете не объект.')
    nodes=geography.get('nodes') or [];routes=geography.get('routes') or []
    if not isinstance(nodes,list) or not isinstance(routes,list):raise WorldPackageProblem('География мира в пакете повреждена: узлы и дороги должны быть списками.')
    if len(nodes)>WORLD_MAX_LOCATIONS:raise WorldPackageProblem('В пакете больше '+str(WORLD_MAX_LOCATIONS)+' узлов карты — файл повреждён.')
    if len(routes)>WORLD_MAX_ROUTES:raise WorldPackageProblem('В пакете больше '+str(WORLD_MAX_ROUTES)+' дорог — файл повреждён.')
    ids=[node.get('id') for node in nodes if isinstance(node,dict)]
    if len(ids)!=len(nodes) or len(set(ids))!=len(ids):raise WorldPackageProblem('Узлы карты в пакете без идентификатора или повторяются: файл повреждён.')
    ids=set(ids)
    for route in routes:
        if not isinstance(route,dict) or route.get('from') not in ids or route.get('to') not in ids:
            raise WorldPackageProblem('Дорога в пакете ведёт в место, которого нет на карте: файл повреждён.')
    npcs=sum(len(place['npc']) for place in locations if isinstance(place.get('npc'),list))
    if npcs>WORLD_MAX_NPCS:raise WorldPackageProblem('В пакете больше '+str(WORLD_MAX_NPCS)+' персонажей — файл повреждён.')
    location=world.get('location')
    if not isinstance(location,str) or not location.strip():raise WorldPackageProblem('В пакете не указано текущее место героя.')
    if location not in names:raise WorldPackageProblem('Текущее место «'+location+'» отсутствует среди мест пакета.')
    elapsed=world.get('elapsed_minutes',0)
    if not isinstance(elapsed,int) or isinstance(elapsed,bool) or elapsed<0:raise WorldPackageProblem('Часы мира в пакете повреждены.')
    characters=raw.get('characters')
    if not isinstance(characters,dict) or not isinstance(characters.get('engine'),dict):
        raise WorldPackageProblem('В пакете нет состояния героя (characters.engine) — без него мир не запустится.')
    if not isinstance(characters.get('hero') or {},dict):raise WorldPackageProblem('Запись героя в пакете повреждена.')
    from .world import WorldSettings
    try:WorldSettings.model_validate((world.get('world') or {}).get('settings') or {})
    except Exception as exc:raise WorldPackageProblem('Настройки мира в пакете не читаются: '+_reason(exc)) from None
    settings=raw.get('settings') if isinstance(raw.get('settings'),dict) else {}
    system=raw.get('system') if isinstance(raw.get('system'),dict) else {}
    normalized=dict(world)
    return dict(format=WORLD_FORMAT,format_version=version,created_at=str(raw.get('created_at') or ''),
        short_name=short_name(raw.get('short_name') or raw.get('name') or 'world'),
        name=str(raw.get('name') or 'Готовый мир')[:100],system=system,
        book=raw.get('book') if isinstance(raw.get('book'),dict) else {},
        settings=settings,world=normalized,characters=characters,
        counts=dict(locations=len(locations),routes=len(routes),npcs=npcs))

def inspect_package(raw):
    """Что лежит в файле: имя кампании, система и совместимость. Ничего не пишет.

    Экран «Мои миры» спрашивает это ДО импорта. Ответ обязан быть и на чужой
    файл: имя, формат и версия читаются поверхностно, а вердикт совместимости
    выносит тот же ``validate_package``, на котором живёт импорт. Один разбор —
    одна причина отказа, и имя файла в этом решении не участвует.
    """
    shallow=raw if isinstance(raw,dict) else {}
    declared=shallow.get('format_version')
    look=dict(name=str(shallow.get('name') or '')[:120],format=str(shallow.get('format') or ''),
        format_version=declared if isinstance(declared,int) and not isinstance(declared,bool) else None)
    try:package=validate_package(raw)
    except WorldPackageProblem as exc:
        return dict(ok=False,compatible=False,compatibility='несовместим с этой сборкой',
            reason=str(exc),system='',**look)
    return dict(ok=True,compatible=True,compatibility='совместим: формат '+str(package['format_version'])
            +' читается этой сборкой',reason='',name=package['name'],system=system_label(package),
        format=package['format'],format_version=package['format_version'],counts=package['counts'],
        created_at=package['created_at'])

def package_state(package):
    """Состояние новой кампании из пакета: без разбора книги и без модели."""
    from .contracts import Hero
    from .nat20_contracts import DurableState
    from .world import ensure
    engine=copy.deepcopy(package['characters']['engine'])
    try:DurableState.model_validate(engine)
    except Exception as exc:
        raise WorldPackageProblem('Состояние героя в пакете не подходит этой сборке движка: '+_reason(exc)
            +'. Пакет собран на другой версии Nat20 — обновите приложение на обеих машинах.') from None
    hero_raw=copy.deepcopy(package['characters'].get('hero') or {})
    hero_raw.setdefault('name',package['name'])
    try:hero=Hero.model_validate(hero_raw)
    except Exception as exc:raise WorldPackageProblem('Запись героя в пакете не подходит этой сборке: '+_reason(exc)) from None
    initial=copy.deepcopy(package['world'])
    initial.pop('counts',None)
    # Кампания новая: счётчик ходов начинается с нуля, а мир и его часы переносятся
    # как есть — игрок продолжает там, где остановился мир, но не «ход №N».
    initial['turn']=0
    initial.setdefault('facts',[]);initial.setdefault('deferred',[])
    initial.setdefault('locations',[]);initial.setdefault('geography',dict(nodes=[],routes=[]))
    initial['hero']=hero.model_dump();initial['nat20']=engine
    preparation=dict(initial.get('preparation') or {})
    # Черновик и подтверждение мира принадлежат другой базе: импорт не должен
    # выглядеть как «эта кампания уже начата из черновика X», иначе повторный
    # запуск подготовки подхватит чужую кампанию.
    for key in ('draft','world_confirmation','source_documents'):preparation.pop(key,None)
    preparation.update(source='world-package',package_format=WORLD_FORMAT,package_version=package['format_version'],
        imported_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),package_name=package['name'],
        book=copy.deepcopy(package.get('book') or {}),mechanics_ruleset='dnd2024')
    initial['preparation']=preparation
    ensure(initial)
    return hero,initial

def import_world_package(store,raw,*,name=''):
    """Создать кампанию из пакета. Разбор книги и вызовы модели не запускаются."""
    from .rulesets import BUILTINS,RulesetRepository
    package=validate_package(raw)
    title=(str(name or '').strip() or package['name'] or 'Готовый мир')[:100]
    hero,initial=package_state(package)
    preset=BUILTINS['dnd2024']
    saved=package['settings'].get('ruleset') if isinstance(package['settings'].get('ruleset'),dict) else {}
    if saved.get('id'):
        try:preset=RulesetRepository(store).get(saved['id'],saved.get('version'))
        except (KeyError,ValueError):preset=BUILTINS['dnd2024']
    current=store.create(title,hero,preset,initial_state=initial)
    return dict(id=current['id'],name=current['name'],backend='nat20',system=system_label(package),
        notice='Мир загружен из файла за один шаг: книга не разбиралась, вызовов модели не было.',
        world=dict(location=initial['location'],counts=package['counts']))

def save_package(store,campaign,*,name=''):
    """Положить мир кампании в библиотеку на сервере (runtime/worlds)."""
    package=export_package(store,campaign);folder=world_folder(store);folder.mkdir(parents=True,exist_ok=True)
    slug=short_name(name or package['name']);stamp=datetime.now().strftime('%Y%m%d-%H%M')
    path=folder/(slug+'-'+stamp+WORLD_SUFFIX);number=2
    while path.exists():path=folder/(slug+'-'+stamp+'-'+str(number)+WORLD_SUFFIX);number+=1
    path.write_text(json.dumps(package,ensure_ascii=False,indent=1),encoding='utf-8')
    return dict(file=path.name,name=package['name'],short=path.name[:-len(WORLD_SUFFIX)],size=path.stat().st_size,
        created_at=package['created_at'],system=system_label(package),counts=package['counts'],broken=False)

LIBRARY_CACHE={}
def library(store):
    """Всё, что лежит в runtime/worlds: имя, система, дата, размер, состояние файла."""
    folder=world_folder(store);folder.mkdir(parents=True,exist_ok=True);rows=[]
    for path in sorted(folder.glob('*'+WORLD_SUFFIX)):
        if path.is_symlink() or not path.is_file():continue
        try:stat=path.stat()
        except OSError:continue
        key=str(path.resolve());stamp=(stat.st_mtime_ns,stat.st_size)
        cached=LIBRARY_CACHE.get(key)
        if cached and cached[0]==stamp:rows.append(dict(cached[1]));continue
        entry=dict(file=path.name,short=path.name[:-len(WORLD_SUFFIX)],size=stat.st_size,saved=stat.st_mtime,broken=False)
        try:
            package=validate_package(_read_package(path))
            entry.update(name=package['name'],system=system_label(package),book=package['book'],
                counts=package['counts'],created_at=package['created_at'])
        except (WorldPackageProblem,OSError,ValueError) as exc:
            # Битый файл виден в списке вместе с причиной: молча пропасть он не
            # должен, иначе игрок ищет сохранённый мир и не находит его.
            entry.update(name=path.name[:-len(WORLD_SUFFIX)],system='',broken=True,problem=str(exc))
        LIBRARY_CACHE[key]=(stamp,entry);rows.append(dict(entry))
    return rows

def library_package(store,name):
    return _read_package(safe_file(store,name))
