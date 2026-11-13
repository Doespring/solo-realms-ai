"""Сверка уровня героя с книгой на старте кампании (L02).

Владелец, 08.11: «в начале игры мастер сравнивает героя игрока с требуемым
уровнем в кампании, если есть несовпадения, то мастер должен попросить игрока
заново собрать героя с требуемым уровнем. Если игрок отказался, то мастер уже
балансирует игру под уровень игрока…».

Книга называет уровень, на который рассчитана (`minimum_level`/`maximum_level`
подготовленного мира). Герой приходит со своим `build.level`. Пока эти два
числа не сведены, Мастер не бросает кубик: он называет оба числа и предлагает
выбор — пересобрать героя на уровне книги или играть как есть, и тогда
опасность боёв считается от уровня героя, а не от книги.

Здесь только числа и текст: уровень героя, требование книги, бюджет опасности
боя и выбор противников из уже зарегистрированных. Правила канона не
отключаются — уменьшается количество и сила врагов, а не правила.

Состояние кампании, которое переживает перезапуск:

* ``state['preparation']['level_requirement']`` — что просит книга
  (пишется один раз при запуске подготовленного мира);
* ``state['balance_override'] = 'player_level'`` — игрок отказался пересобирать
  героя, Мастер ведёт опасность под его уровень;
* ``state['level_gate']`` — чем кончилась сверка, каким способом ведётся игра
  и что Мастер об этом сказал.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from pydantic import Field

from .contracts import Strict


class Decision(Strict):
    """Ответ экрана: «играем так» — одно журнальное событие, без тела игры."""

    request_id: str = Field(min_length=1, max_length=80)


class Rebuild(Strict):
    """Ответ экрана: собранный на уровне книги герой вместо прежнего."""

    request_id: str = Field(min_length=1, max_length=80)
    build: dict
    prepared_spells: list[str] = Field(default_factory=list)
    name: str = Field(default='', max_length=80)

#: Сколько «единиц опасности» (CR) выдерживает один герой уровня N.
#: Одиночка 1-го уровня — четверть CR: один слабый враг, а не отряд.
DANGER_PER_LEVEL = 0.25

#: Слова игрока. Разбираются до модели: отказ «играем так» — это факт
#: кампании, а не свободное намерение, которое Мастер волен переиначить.
REBUILD_WORDS = re.compile(r'\bпересоб|собери\s+героя\s+заново|\bпересборк|\bпересобра', re.I)
ACCEPT_WORDS = re.compile(r'\bиграем\s+так|\bиграем\s+как\s+есть|\bиграть\s+так\b|'
                          r'\bпринимаю\b|\bя\s+согласен\b', re.I)


def _level(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 20 else None


def requirement(state):
    """Что просит книга: минимум, максимум, имя и откуда это взято.

    Сначала — то, что записал запуск подготовленного мира. Кампания,
    начатая раньше, этого поля не имеет: тогда требование берётся из
    подключённого сценария (``adventure.definition``). Ничего не выдумывается:
    книга, не назвавшая уровень, требования не даёт.
    """
    preparation = state.get('preparation') or {}
    written = preparation.get('level_requirement') or {}
    minimum = _level(written.get('minimum'))
    if minimum:
        return dict(book=str(written.get('book') or ''), minimum=minimum,
                    maximum=_level(written.get('maximum')),
                    party=_level(written.get('party')),
                    source=str(written.get('source') or 'preparation'))
    adventure = state.get('adventure') or {}
    definition = adventure.get('definition') or {}
    minimum = _level(definition.get('minimum_level'))
    if minimum:
        return dict(book=str(definition.get('title') or adventure.get('title') or ''),
                    minimum=minimum, maximum=_level(definition.get('maximum_level')),
                    party=_level(definition.get('recommended_party')), source='scenario')
    return None


def install_requirement(state, *, book='', minimum=None, maximum=None, party=None,
                        source='preparation'):
    """Записать требование книги в кампанию (одно место — запуск мира)."""
    minimum = _level(minimum)
    if not minimum:
        return None
    written = dict(book=str(book or ''), minimum=minimum, maximum=_level(maximum),
                   party=_level(party), source=source)
    state.setdefault('preparation', {})['level_requirement'] = written
    return written


def hero_level(state):
    """Уровень героя кампании: сначала лист, потом сборка движка."""
    level = _level((state.get('hero') or {}).get('level'))
    if level:
        return level
    runtime = state.get('nat20') or {}
    build = (runtime.get('definition') or {}).get('build') or {}
    return _level(build.get('level')) or 1


def gap(level, required):
    """'lower' — герой слабее книги, 'higher' — сильнее, '' — совпало."""
    if level < required['minimum']:
        return 'lower'
    if required.get('maximum') and level > required['maximum']:
        return 'higher'
    return ''


def book_name(required):
    return 'Книга «' + required['book'] + '»' if required.get('book') else 'Книга'


def ask_text(level, required):
    """Ход Мастера до первого броска: оба числа и оба пути."""
    who = book_name(required)
    tail = ('Пересобери героя на ' + str(required['minimum']) + '-й уровень — или играем как '
            'есть: тогда я буду подгонять опасность под тебя, а не под книгу.')
    if gap(level, required) == 'higher':
        return ('Книга «' + required['book'] + '» рассчитана на ' + str(required['minimum']) + '-й уровень, '
                'твой герой ' + str(level) + '-го — для книги это слишком много. ' + tail) if required.get('book') \
            else ('Книга рассчитана на ' + str(required['minimum']) + '-й уровень, твой герой '
                  + str(level) + '-го — для книги это слишком много. ' + tail)
    head = (who + ' рассчитана на ' + str(required['minimum']) + '-й уровень, твой герой '
            + str(level) + '-го. ')
    return head + tail


def accept_text(level, required, encounters=None):
    """Что Мастер меняет, когда игрок выбрал «играем так».

    Называет способ и его числа: сколько врагов выйдет вместо книжного отряда.
    """
    fight = fight_summary(encounters)
    parts = ['Играем как есть. С этого хода опасность считается от твоего ' + str(level)
             + '-го уровня, а не от ' + str(required['minimum']) + '-го:']
    if fight:
        parts.append('в бою против тебя выйдет ' + fight + ', а не отряд на четырёх героев '
                     + str(required['minimum']) + '-го уровня.')
    else:
        parts.append('боя, посильного одному герою ' + str(level) + '-го уровня, у книги сейчас нет — '
                     'этот бой я не ставлю.')
    parts.append('Задания вне сюжета дадут опыт, чтобы ты догнал книгу.')
    return ' '.join(parts)


def rebuild_text(level, required):
    return ('Герой пересобран на ' + str(level) + '-й уровень: лист, умения и снаряжение новые, '
            'старого героя в кампании больше нет. ' + book_name(required) + ' ждёт именно этот уровень.')


def projection(state):
    """Что экран и Мастер знают о сверке прямо сейчас; ничего не пишет."""
    required = requirement(state)
    if not required:
        return None
    level = hero_level(state)
    difference = gap(level, required)
    gate = state.get('level_gate') or {}
    override = state.get('balance_override') or ''
    decision = str(gate.get('decision') or override or '')
    result = dict(active=True, book=required['book'], required=required['minimum'],
                  maximum=required['maximum'], party=required['party'], hero_level=level,
                  gap=difference, aligned=not difference, decided=bool(decision),
                  decision=decision or None, override=override or None,
                  ask=ask_text(level, required) if difference and not decision else '',
                  decision_text=str(gate.get('text') or '') if decision else '',
                  method=str(gate.get('method') or '') if decision else '',
                  options=([dict(id='rebuild', label='Пересобрать героя на ' + str(required['minimum']) + '-й уровень'),
                            dict(id='accept', label='Играем так')]
                           if difference and not decision else []))
    return result


def phrase(text):
    """'rebuild' | 'accept' | None — что игрок сказал словами."""
    value = str(text or '').strip().replace('ё', 'е')
    if not value:
        return None
    if REBUILD_WORDS.search(value):
        return 'rebuild'
    if ACCEPT_WORDS.search(value):
        return 'accept'
    return None


def guard(state, action):
    """Отказ до первого броска: пока сверка не решена, бой не открывается."""
    kind = getattr(action, 'kind', None) or (action or {}).get('kind')
    if kind != 'start_combat':
        return
    gate = projection(state)
    if gate and gate['gap'] and not gate['decided']:
        raise ValueError(gate['ask'])


def mark(state, decision, *, text='', method=''):
    """Записать решение игрока в кампанию: поле переживает перезапуск."""
    gate = projection(state)
    if not gate:
        raise ValueError('Книга не назвала уровень: сверять нечего.')
    state['level_gate'] = dict(book=gate['book'], required=gate['required'],
                               maximum=gate['maximum'], hero_level=gate['hero_level'],
                               gap=gate['gap'], decision=decision, method=method,
                               text=text, at=state.get('turn', 0))
    if decision == 'player_level':
        state['balance_override'] = 'player_level'
    elif state.pop('balance_override', None):
        # Герой пересобран на уровень книги: подгонять опасность больше не под
        # кого — опасность снова считается по книге, а не по прежнему герою.
        pass
    return state['level_gate']


# --- опасность боя -------------------------------------------------------

DANGER_NOTE = 'Опасность считается от уровня героя: '


def danger_budget(level):
    """Сколько CR выдерживает одиночка этого уровня."""
    return round(DANGER_PER_LEVEL * max(1, int(level or 1)), 3)


@lru_cache(maxsize=2048)
def monster_profile(slug):
    """CR и имя монстра из закреплённого датасета; None — сила не подтверждена."""
    from .nat20_prototype import DATA
    slug = str(slug or '')
    if not slug or any(character not in 'abcdefghijklmnopqrstuvwxyz0123456789-'
                       for character in slug):
        return None
    path = DATA / 'monsters' / (slug + '.json')
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        return None
    challenge = raw.get('cr')
    if isinstance(challenge, bool) or not isinstance(challenge, (int, float)):
        return None
    return dict(slug=slug, name=str(raw.get('name') or slug), cr=float(challenge),
                hp=int(raw.get('hp') or 0))


def monster_cr(slug) -> float | None:
    profile = monster_profile(slug)
    if not profile:
        return None
    return float(profile['cr'])


def monster_name(slug) -> str:
    profile = monster_profile(slug)
    return str(profile['name']) if profile else str(slug or 'враг')


def fight_summary(encounters):
    """'2 × Kobold Warrior' — состав боя словами, без выдумки."""
    for key in sorted(encounters or {}):
        actors = (encounters[key] or {}).get('actors') or []
        if not actors:
            continue
        counts = {}
        for actor in actors:
            name = monster_name(actor.get('monster'))
            counts[name] = counts.get(name, 0) + 1
        return ', '.join(str(count) + ' × ' + name for name, count in counts.items())
    return ''


def _foes(count):
    if count % 10 == 1 and count % 100 != 11:
        return str(count) + ' враг'
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return str(count) + ' врага'
    return str(count) + ' врагов'


def scale_encounter(encounter, level):
    """Один бой: враги, посильные одиночке этого уровня.

    Бюджет опасности — четверть CR на уровень героя, и врагов не больше, чем
    уровень героя: число и сила врагов считаются от героя, а не от книги.
    Возвращает (encounter, note) или (None, причина): выдуманного врага не
    бывает — если в бою нет никого, кто проходит бюджет, бой не ставится.
    """
    budget = danger_budget(level)
    cap = max(1, int(level or 1))
    actors = list((encounter or {}).get('actors') or [])
    if not actors:
        return None, 'Пустой бой не ставится.'
    chosen, spent, dropped = [], 0.0, []
    for actor in actors:
        strength = monster_cr(actor.get('monster'))
        if strength is None:
            dropped.append(monster_name(actor.get('monster')))
            continue
        if len(chosen) >= cap or spent + strength > budget + 1e-9:
            dropped.append(monster_name(actor.get('monster')))
            continue
        chosen.append(actor)
        spent += strength
    if not chosen:
        return None, ('На ' + str(level) + '-м уровне герой один: ни один враг этого боя '
                      '(' + ', '.join(sorted(set(dropped))) + ') ему не по силам, поэтому бой не ставится.')
    scaled = dict(encounter)
    scaled['actors'] = chosen
    note = ''
    if dropped:
        note = (DANGER_NOTE + _foes(len(chosen)) + ' вместо ' + _foes(len(actors))
                + ' — бюджет ' + ('%g' % budget) + ' CR на одного героя ' + str(level) + '-го уровня.')
    return scaled, note


def scale_encounters(encounters, level):
    """Все бои кампании под уровень героя. Возвращает (бои, записи для журнала)."""
    kept, notes = {}, []
    for key in sorted(encounters or {}):
        scaled, note = scale_encounter(encounters[key], level)
        if scaled is None:
            notes.append('Бой «' + str(key) + '» снят. ' + note)
            continue
        kept[key] = scaled
        if note:
            notes.append('Бой «' + str(key) + '»: ' + note)
    return kept, notes


__all__ = ['requirement', 'install_requirement', 'hero_level', 'gap', 'ask_text',
           'accept_text', 'rebuild_text', 'projection', 'phrase', 'guard', 'mark',
           'danger_budget', 'monster_cr', 'monster_name', 'fight_summary',
           'scale_encounter', 'scale_encounters', 'rebuild_context', 'backfill_requirements',
           'register_level_gate_api', 'DANGER_PER_LEVEL']


# --- экран и слова -------------------------------------------------------

def rebuild_context(state, preparation):
    """Что нужно редактору героя, чтобы открыться на уровне книги.

    Черновик книги уже связан с кампанией, поэтому редактор берёт его ревизию
    и отпечаток прогона, а не создаёт второй старт.
    """
    draft = str((state.get('preparation') or {}).get('draft') or '')
    if not draft or preparation is None:
        return None
    try:
        from .prototype_prepared import prepared_start_readiness
        readiness = prepared_start_readiness(preparation, draft)
    except Exception:  # нет черновика в этом профиле — редактор закрыт честно
        return None
    if not readiness.get('ready'):
        return None
    return dict(draft_id=draft, revision=readiness.get('revision'), run_id=readiness.get('run_id'),
                level=readiness.get('hero_level') or readiness.get('minimum_level'),
                minimum_level=readiness.get('minimum_level'),
                maximum_level=readiness.get('maximum_level'),
                party=readiness.get('recommended_party'),
                book=str(readiness.get('book') or ''))


def backfill_requirements(store, preparation, *, context_for=None, limit=200):
    """Кампании, начатые до L02: требование книги дописывается один раз.

    Новый старт подготовленного мира записывает требование сам. У кампаний,
    начатых раньше, поля нет, и сверка на старте молчала бы ровно там, где
    владелец её и ждёт, — в уже идущей игре. Требование восстанавливается из
    того же черновика книги, без модели и без хода: одно журнальное событие,
    чтобы после перезапуска было видно, откуда взялся уровень.

    Ничего не выдумывает: нет черновика, мир не подтверждён или уровень не
    назван — кампания остаётся без требования, как и была.
    """
    if store is None:
        return []
    resolver = context_for or (lambda state: rebuild_context(state, preparation))
    try:
        with store.connection() as connection:
            rows = connection.execute(
                "SELECT id FROM campaigns WHERE json_extract(state,'$.preparation.draft') IS NOT NULL "
                "AND json_extract(state,'$.preparation.level_requirement') IS NULL LIMIT ?",
                (int(limit),)).fetchall()
    except Exception:  # база без нужных полей — восстановление просто нечего делать
        return []
    filled = []
    for row in rows:
        campaign = row['id']
        try:
            context = resolver(store.campaign(campaign)['state'])
        except Exception:
            continue
        minimum = _level((context or {}).get('minimum_level'))
        if not minimum:
            continue
        book = str((context or {}).get('book') or '')
        text = ('Требование книги восстановлено: ' + book_name(dict(book=book, minimum=minimum))
                + ' рассчитана на ' + str(minimum) + '-й уровень.')

        def transition(state, context=context, minimum=minimum, text=text):
            written = install_requirement(state, book=context.get('book') or '',
                                          minimum=minimum, maximum=context.get('maximum_level'),
                                          party=context.get('party'), source='backfill')
            if written is None:
                return []
            return [dict(type='scripted', kind='level_gate', text=text)]

        try:
            store.record_scenario(campaign, 'level-requirement-backfill',
                                  'Требование книги восстановлено', transition,
                                  counts_as_turn=False)
        except Exception:
            continue
        filled.append(campaign)
    return filled


def register_level_gate_api(app, store, game, preparation=None):
    """Сверка уровня на старте: чтение, отказ и пересборка (L02)."""
    import asyncio
    from fastapi import HTTPException

    def campaign_or_404(campaign):
        try:
            return store.campaign(campaign)
        except KeyError:
            raise HTTPException(404, dict(code='campaign_not_found',
                message='Кампания не найдена: возможно, её удалили. Откройте список кампаний и выберите другую.')) from None

    @app.get('/api/campaigns/{campaign}/level-gate')
    def read(campaign: str):
        current = campaign_or_404(campaign)
        gate = projection(current['state'])
        return dict(level_gate=gate, rebuild=rebuild_context(current['state'], preparation))

    @app.post('/api/campaigns/{campaign}/level-gate/accept')
    async def accept(campaign: str, body: Decision):
        current = campaign_or_404(campaign)
        from .nat20_api import Nat20GameEngine
        async with game.operation:
            result = await asyncio.to_thread(Nat20GameEngine(store).accept_player_level,
                                             campaign, request_id=body.request_id)
        result['rebuild'] = rebuild_context(store.campaign(campaign)['state'], preparation)
        return result

    @app.post('/api/campaigns/{campaign}/level-gate/rebuild')
    async def rebuild(campaign: str, body: Rebuild):
        current = campaign_or_404(campaign)
        from .nat20_api import Nat20GameEngine
        async with game.operation:
            result = await asyncio.to_thread(Nat20GameEngine(store).rebuild_hero, campaign,
                                             request_id=body.request_id, build=body.build,
                                             prepared_spells=body.prepared_spells, name=body.name)
        result['rebuild'] = None
        return result

