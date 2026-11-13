"""Player projection and cached, disposable legality probes; no rule arithmetic."""
from collections import OrderedDict
from copy import deepcopy

from .game_commands import GameCommand
from .nat20_api import Nat20GameEngine
from .nat20_content import COVER_NAMES, digest


def _combat_view(runtime):
    """Native grid facts the public snapshot does not carry."""
    combat = runtime.get('combat') or {}
    return combat.get('view') or {}, (combat.get('initial') or {}).get('grid') or {}


def _cell_distance(first, second):
    ax, ay = (int(part) for part in first.split(','))
    bx, by = (int(part) for part in second.split(','))
    return max(abs(ax - bx), abs(ay - by))


def _side(runtime):
    view, _ = _combat_view(runtime)
    hp = {actor['entity_id']: actor.get('hp_current') for actor in view.get('initiative') or []}
    hp.update(view.get('tracked_hp') or {})
    return view, (view.get('actor_zone') or {}), hp, set(view.get('dead_ids') or [])


def _cell_label(cell):
    """Клетка сетки «col,row» так, как её видит игрок: нумерация с единицы."""
    try:
        x, y = (int(part) for part in str(cell).split(','))
    except (TypeError, ValueError):
        return str(cell or '')
    return f'{x+1}, {y+1}'


def terrain_label(grid, cell):
    """Подпись местности клетки: укрытие или непроходимость из разметки сцены.

    r206: правила укрытия и препятствий считались движком (r187), но авторская
    сцена не была размечена, а экран разметку не называл — игрок не видел, почему
    атака промахнулась и почему шаг в клетку отбит. Здесь только чтение разметки
    (`cover_cells`/`blocked_cells`); степень укрытия между клетками по-прежнему
    считает шов движка, своего расчёта в хосте нет.
    """
    if not cell:
        return ''
    grid = grid if isinstance(grid, dict) else {}
    if cell in (grid.get('blocked_cells') or ()):
        return 'непроходимо'
    degree = (grid.get('cover_cells') or {}).get(cell)
    if degree:
        return 'укрытие: ' + str(COVER_NAMES.get(degree) or degree)
    return ''


def terrain_summary(grid):
    """Строка о местности боя: разметка видна и там, куда шаг не предлагают."""
    grid = grid if isinstance(grid, dict) else {}
    cover = grid.get('cover_cells') or {}
    blocked = list(grid.get('blocked_cells') or ())
    if not cover and not blocked:
        return None
    parts = []
    if cover:
        groups = {}
        for cell, degree in sorted(cover.items()):
            groups.setdefault(COVER_NAMES.get(degree, degree), []).append(_cell_label(cell))
        parts.append('укрытие — ' + '; '.join(
            f'{name}: ' + ', '.join(cells) for name, cells in groups.items()))
    if blocked:
        parts.append('непроходимо — ' + ', '.join(_cell_label(cell) for cell in blocked))
    return 'Местность: ' + '. '.join(parts) + '.'


def adjacent_enemies(runtime):
    """Живые враги в 5 футах от героя — цель Захвата и Толчка (SRD 5.2)."""
    view, zones, hp, dead = _side(runtime)
    hero = zones.get('hero')
    if not hero:
        return []
    names = {actor['entity_id']: actor.get('name') for actor in view.get('initiative') or []}
    found = []
    for enemy in view.get('encounter_ids') or []:
        if enemy in dead or (hp.get(enemy) or 0) <= 0 or not zones.get(enemy):
            continue
        if _cell_distance(hero, zones[enemy]) <= 1:
            found.append(dict(id=enemy, name=names.get(enemy) or enemy))
    return found


def hero_conditions(runtime):
    """Состояния героя ИМЕНАМИ строк (пусто вне боя).

    Проекция боя отдаёт `conditions` записями ActiveCondition (словарь с
    ключом `condition`). До r197 функция возвращала эти записи как есть,
    поэтому `'grappled' in hero_conditions(...)` был ВСЕГДА False и кнопка
    «Вырваться из захвата» не появлялась ни разу. Имена — единственная
    форма, которую проверяют вызывающие (r197: `'prone'`).
    """
    view, _ = _combat_view(runtime)
    row = next((actor for actor in view.get('initiative') or []
                if actor.get('entity_id') == 'hero'), {})
    return [entry.get('condition') if isinstance(entry, dict) else str(entry)
            for entry in (row.get('conditions') or []) if entry]


def grappled_by(runtime):
    """Кто ДЕРЖИТ героя — из записи состояния, а не из соседства (r198).

    Захват ставит движок: на `ActiveCondition` героя лежит `source_effect_id`, а
    у эффекта `origin` вида `grapple:<activity>:<id>` (безоружный захват героя,
    `_handle_grapple`) или `cast:<эффект>:<id>` (r199: типизированный эффект
    монстра — Quick Grapple, Constrict).
    Имя захватчика — единственный настоящий ответ на вопрос «от кого
    освобождаться». До r198 экран брал первого ПОПАВШЕГОСЯ соседнего врага и
    подписывал кнопку его именем: в живом прогоне (сид 1, штатная кампания)
    кнопка звала «Вырваться из захвата: Goblin Warrior», хотя держал героя не
    гоблин. `None` — источнике в снимке нет (тогда цель не выдумываем).
    """
    view, _ = _combat_view(runtime)
    row = next((actor for actor in view.get('initiative') or []
                if actor.get('entity_id') == 'hero'), {})
    entries = [entry for entry in row.get('conditions') or [] if isinstance(entry, dict)]
    names = {actor['entity_id']: actor.get('name') for actor in view.get('initiative') or []}
    effect_ids = {entry.get('source_effect_id') for entry in entries
                  if entry.get('condition') == 'grappled' and entry.get('source_effect_id')}
    carried = []
    for effect in (runtime.get('combat') or {}).get('active_effects') or []:
        if effect.get('id') not in effect_ids:
            continue
        parts = str(effect.get('origin') or '').split(':', 2)
        # r199: у источника эффекта ДВЕ формы, обе — конвенция движка
        # (`orchestrator._ENTITY_ORIGIN_PREFIXES`): `grapple:<приём>:<кто держит>`
        # от безоружного захвата героя и `cast:<эффект>:<кто наложил>` от
        # типизированного эффекта монстра (Quick Grapple/Constrict). Третья
        # часть в обеих — существо; берём её, только если оно есть в снимке,
        # чтобы `cast:<эффект>:<не-существо>` не превратился в цель.
        if len(parts) != 3 or not parts[2] or parts[2] not in names:
            continue
        if parts[0] == 'grapple':
            return dict(id=parts[2], name=names.get(parts[2]) or parts[2])
        if parts[0] == 'cast':
            carried.append(dict(id=parts[2], name=names.get(parts[2]) or parts[2]))
    if carried:
        return carried[0]
    for entry in entries:
        # Второй носитель источника: `implied:*` — метка, а не существо.
        source = str(entry.get('source_entity_id') or '') if entry.get('condition') == 'grappled' else ''
        if source and not source.startswith('implied:') and source in names:
            return dict(id=source, name=names.get(source) or source)
    return None


def help_target(runtime):
    """SRD 5.2 Help distracts an ENEMY within 5 ft for an ally's next attack.

    Offered only where the grant can actually be spent: a second living party
    member exists and a living enemy stands within 5 ft (that enemy is the
    target, not the ally). Measured: without an ally the pinned engine still
    accepts `help`, but the grant expires at the helper's own next
    TurnStarted, so the Action would buy nothing.
    """
    view, zones, hp, dead = _side(runtime)
    allies = [member for member in view.get('party_ids') or []
              if member != 'hero' and member in zones and member not in dead and (hp.get(member) or 0) > 0]
    if not allies:
        return None
    return next(iter(adjacent_enemies(runtime)), None)


def hide_rule_met(runtime):
    """Mirror of the pinned Hide gate: own cell in three-quarters/total cover,
    heavy obscurement or darkness (orchestrator `_handle_hide`)."""
    view, grid = _combat_view(runtime)
    cell = (view.get('actor_zone') or {}).get('hero')
    if not cell:
        return False
    if cell in (grid.get('blocked_cells') or []) or (grid.get('cover_cells') or {}).get(cell) in ('three_quarters', 'total'):
        return True
    if (grid.get('obscurement_cells') or {}).get(cell) == 'heavy':
        return True
    return (grid.get('lighting') or {}).get(cell, grid.get('default_lighting') or 'bright') == 'dark'


def native_candidates(runtime):
    """Native actions offered only where the pinned engine accepts them."""
    candidates = [(name, dict(kind='native_action', operation=operation)) for name, operation in (
        ('Уклонение', 'dodge'), ('Отход', 'disengage'), ('Встать', 'stand_up'))]
    # r197: движок принимает добровольное «лечь» (SRD 5.2 Prone: состояние
    # без траты действия). Кнопки не было — игрок не мог лечь, пока его не
    # собьют Толчком. Уже лежащему кнопка не нужна: встать — отдельная.
    if 'prone' not in hero_conditions(runtime):
        candidates.append(('Лечь', dict(kind='native_action', operation='drop_prone')))
    # r187: Захват и Толчок считаются движком, но кнопки не было — игрок их не
    # находил. SRD 5.2: цель не больше чем на одну категорию крупнее, в 5 футах.
    for enemy in adjacent_enemies(runtime):
        candidates.append(('Захватить: ' + enemy['name'],
                           dict(kind='native_action', operation='grapple', target_id=enemy['id'])))
        candidates.append(('Сбить с ног: ' + enemy['name'],
                           dict(kind='native_action', operation='shove', target_id=enemy['id'],
                                shove_push=False)))
        candidates.append(('Оттолкнуть: ' + enemy['name'],
                           dict(kind='native_action', operation='shove', target_id=enemy['id'],
                                shove_push=True)))
    if 'grappled' in hero_conditions(runtime):
        # r198: освобождаться нужно от ТОГО, КТО ДЕРЖИТ, а не от соседнего
        # врага. Прежнее условие требовало врага в 5 футах: в живом прогоне
        # (сид 1, штатная кампания) герой оставался схваченным с движением 0 и
        # БЕЗ кнопки вовсе, потому что гоблин уже был схвачен героем сам.
        # Движок читает КС из состояния героя и `target_id` не использует —
        # цель нужна подписи и журналу, поэтому без источника не выдумываем её.
        grappler = grappled_by(runtime)
        candidates.append(('Вырваться из захвата' + (': ' + grappler['name'] if grappler else ''),
                           dict(kind='native_action', operation='escape_grapple',
                                **({'target_id': grappler['id']} if grappler else {}))))
    enemy = help_target(runtime)
    if enemy:
        candidates.append(('Помощь: помешать ' + enemy['name'],
                           dict(kind='native_action', operation='help', target_id=enemy['id'])))
    # r129b — Подготовки в списке нет: замерено прогоном
    # (tests/test_nat20_help_ready_hide.py). Пин разрешает только
    # предустановленное ЗАКЛИНАНИЕ: `ready` + spell_id='shield' даёт по
    # триггеру reaction_triggered/spell_cast, а `ready` без заклинания
    # (подготовленная атака оружием) тратит Действие и снимает запись
    # очереди без разрешения — ни события, ни потраченной реакции.
    # Кнопка, называющая один триггер, обещала бы механику, которой нет;
    # честная кнопка будущего называет заклинание реакции и требует, чтобы
    # герой его знал и имел слот. Хост операцию `ready` тоже отказывает
    # (known_issue('actions','ready')), чтобы чат-путь не тратил Действие.
    if hide_rule_met(runtime):
        candidates.append(('Засада', dict(kind='native_action', operation='hide')))
    if 'second-wind' in (runtime.get('definition') or {}).get('features', {}):
        candidates.append(('Второе дыхание', dict(kind='native_action', operation='use_feature',
                                                  feature_id='second-wind', target_id='hero')))
    return candidates

class Nat20Screen:
    def __init__(self):
        self.cache = OrderedDict()

    def read_only(self, current, report):
        """Кампания читается, но не играется: история видна, ввод заперт причиной.

        Механику экран не считает и здесь: хиты героя берутся из сохранения как
        есть, команд нет вовсе. Причина и три действия (копия / проверка /
        экспорт) едут в блоке ``compatibility`` — игрок видит их ДО ввода, а не
        получает английский отказ после нажатия.
        """
        hero = current['state'].get('hero') or {}
        resources = []
        for key, value in (hero.get('resources') or {}).items():
            if not isinstance(value, dict):
                continue
            resources.append(dict(id=key, label=str(value.get('label') or key),
                                  current=value.get('current'), maximum=value.get('maximum')))
        hp = hero.get('hp') or 0
        # Список действий берём у сервера: обещать кнопку, которую гейт одного
        # бэкенда отобьёт, нельзя (критик A02).
        labels = [str(action.get('label') or '') for action in report.get('actions') or []]
        message = (report['summary'] + ' Ввод заперт, история читается. '
                   + ('Доступно: ' + ', '.join('«' + label + '»' for label in labels) + '.'
                      if labels else 'Действий нет — только чтение.'))
        return dict(hero=dict(name=str(hero.get('name') or 'Герой'), hp=hp,
                              max_hp=hero.get('max_hp') or hp,
                              conditions=list(hero.get('conditions') or []),
                              resources=resources, hit_dice=hero.get('hit_dice') or 0),
                    combat=None, commands=[],
                    terminal=dict(code='compatibility_required', message=message),
                    settings=None, out=None, compatibility=report)

    def project(self, current):
        # All committed state/guards participate: undo/restore and rule changes
        # cannot reuse availability from another version or campaign.
        key = digest([current['id'], current['version'], current['state']])
        if key in self.cache:
            self.cache.move_to_end(key)
            return deepcopy(self.cache[key])
        # A02: совместимость проверяется ДО ввода. Старая запись не валит экран
        # английским исключением из `_bound`: она открывается только для чтения,
        # а причина и три действия приходят в пакете сцены.
        from .nat20_compat import inspect as compatibility
        report = compatibility(current)
        if report['status'] != 'ok':
            return self.read_only(current, report)
        backend = Nat20GameEngine(None)
        runtime = backend._bound(current)
        snapshot = backend.visible(backend.snapshot(current))
        hero = next(a for a in snapshot['actors'] if a['id'] == 'hero')
        labels = {'feature_use:second-wind': 'Второе дыхание · потрачено',
                  'movement_remaining': 'Осталось движения, футы', 'attacks_remaining': 'Осталось атак',
                  'action_available': 'Действие', 'bonus_action_available': 'Бонусное действие', 'reaction_available': 'Реакция',
                  'death_save:successes':'Спасброски от смерти · успехи','death_save:failures':'Спасброски от смерти · провалы',
                  'death_save:is_stable':'Стабилизирован','dead':'Смерть подтверждена Nat20','exhaustion':'Истощение'}
        hero_view = dict(name=hero['name'], hp=hero['hp']['current'], max_hp=hero['hp']['maximum'],
                         conditions=hero['conditions'], hit_dice=hero['resources']['hit_dice'],
                         resources=[dict(id=k, label=labels.get(k, k), current=v, maximum=None)
                                    for k, v in hero['resources'].items() if k != 'hit_dice'])
        battle = None
        candidates = []
        if runtime['combat']:
            native = runtime['combat']['view']
            _, grid = _combat_view(runtime)
            initiatives = {a['entity_id']: a['initiative'] for a in native['initiative']}
            battle = dict(active=True, round=snapshot['combat']['round'], current_actor=snapshot['combat']['turn'],
                          actors=[dict(id=a['id'], name=a['name'],
                                       team='hero' if a['id'] in (native.get('party_ids') or ('hero',)) else 'enemy',
                                       hp=a['hp']['current'], max_hp=a['hp']['maximum'], conditions=a['conditions'],
                                       initiative=initiatives[a['id']]) for a in snapshot['actors']],
                          terrain=terrain_summary(grid))
            for actor in snapshot['actors']:
                if actor['id'] == 'hero':
                    continue
                candidates.append(('Атаковать: ' + actor['name'], dict(kind='attack', target=actor['id'])))
                # Coordinates are candidate destinations, not a path/range solver.
                # Native mechanics alone decides which of them are legal.
                x, y, z = actor['position']
                for px, py in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                    if (px, py, z) == tuple(hero['position']) or px < 0 or py < 0:
                        continue
                    label = f'Перейти: {px+1}, {py+1}'
                    # r206: клетка называет свою местность, иначе укрытие и
                    # препятствие существуют в правилах, но не на экране.
                    tag = terrain_label(grid, f'{px},{py}')
                    if tag:
                        label += ' · ' + tag
                    candidates.append((label, dict(kind='move', position=dict(x=px, y=py, z=z))))
            candidates += [('Рывок', dict(kind='dash')),
                           ('Закончить ход', dict(kind='end_turn')), ('Завершить бой', dict(kind='end_combat'))]
            from .nat20_prototype import experimental
            # r194: посчитанные движком действия нужны игроку и в штатной
            # сцене, а не только в экспериментальной. Экран только называет
            # кандидатов; доступность режет проба движка ниже
            # (`mode=availability`), поэтому кнопка появляется там, где
            # правило действительно выполнено.
            if experimental(runtime.get('definition')):
                # r175: умения базового класса, у которых движок уже даёт
                # эффект, а до игры не доходило: «кому» кость, «в какого
                # зверя», какую нежить в 30 фт, варианты Сосредоточенности,
                # безоружный и бонусный безоружный удар монаха. Список строится
                # по условию правила, а не по переписи умений.
                from .nat20_content import class_feature_choices
                candidates += class_feature_choices(runtime)
            else:
                candidates.append(('Второе дыхание',dict(kind='use_feature',feature_id='second-wind')))
            candidates += native_candidates(runtime)
        else:
            from .nat20_content import SCENE_ACTIONS, SCENE_CHECKS, SKILL_NAMES
            from .scene_actions import study_facts
            # Laying a confirmed map or handout on the table is a host turn
            # (`adventure_handout`, resolved in Game.play_command before any model
            # call), not a Nat20 action: the engine has no `handout` intent, so a
            # screen button here would offer a command the backend must refuse.
            # This list stays the engine's own actions.
            # Scene-action check ids are offered as their own action below, never
            # as two buttons for the same roll. A check the book printed is
            # labelled the way the book prints it (its skill and its DC), so the
            # player can ask for it by name; the engine still sees the id.
            from .source_checks import current_checks, label as check_label
            printed = current_checks(current['state'])
            candidates = [('Проверить: ' + (check_label(printed[check]) if check in printed else check),
                           dict(kind='check', check_id=check))
                          for check in snapshot['available_checks'] if check not in SCENE_CHECKS]
            # PHB 2024 Influence/Study/Search: one candidate per (action, skill).
            # Study appears only while the scene still holds a fact the host has
            # not shown — the same condition that refuses the command in preflight.
            from . import social
            actors = social.present(current['state'])
            for action_id, scene_action in SCENE_ACTIONS.items():
                if action_id == 'study' and not study_facts(current['state']):
                    continue
                # r179: Влияние адресуется существу. Нет НИПа — кнопки нет;
                # один — проверка без имени; несколько — имя в подписи, и
                # команда несёт npc_id, чтобы хост не выбирал за игрока.
                targets = actors if action_id == 'influence' else [None]
                if action_id == 'influence' and not targets:
                    continue
                for scene_skill in scene_action['skills']:
                    for npc in targets:
                        label = scene_action['name'] + ': ' + SKILL_NAMES[scene_skill]
                        parameters = dict(kind='scene_action', action_id=action_id, skill=scene_skill)
                        if npc is not None and len(targets) > 1:
                            label += ' — ' + str(npc.get('name') or npc.get('id') or '?')
                            parameters['npc_id'] = npc.get('id') or npc.get('name')
                        candidates.append((label, parameters))
            # Offering a made-up 'basic' encounter advertised a fight the
            # engine had never registered; it was then rejected at execute time.
            encounters=runtime['definition']['encounters'] if runtime.get('definition') else {}
            candidates += [('Начать столкновение: '+key,dict(kind='start_combat',encounter_id=key)) for key in encounters]
            candidates += [('Короткий отдых', dict(kind='short_rest', hit_dice=0)),
                           ('Короткий отдых · потратить кость хитов', dict(kind='short_rest', hit_dice=1)),
                           ('Длительный отдых с пищей и водой', dict(kind='long_rest', food_water=True))]

        commands = []
        valid_candidates = []
        for label, action in candidates:
            command = GameCommand(request_id='screen-availability', expected_version=current['version'], action=action)
            try:
                backend._preflight(current, command)
            except ValueError:
                continue
            commands.append(command.model_dump(mode='json'))
            valid_candidates.append((label, action))
        # One disposable process, with independent replays for each candidate.
        # Same native validation as execution; no speculative state is committed.
        response = backend._worker(dict(mode='availability', state=runtime, commands=commands, session_id=current['id'],
                                        scene_checks=backend._scene_checks(current),
                                        balance_override=current['state'].get('balance_override'),
                                        lethal_risk_declared=backend._lethal_risk_declared(current)))
        if len(response['available']) != len(commands):
            raise ValueError('Invalid native availability response')
        available = [dict(label=label, kind='engine', parameters=action, expected_version=current['version'])
                     for (label, action), allowed in zip(valid_candidates, response['available']) if allowed]
        if current['state'].get('lantern_scene'):
            for command in available:
                if command['parameters']['kind'] == 'start_combat':command['label'] = 'Войти во двор · начать бой'
        for index, command in enumerate(available):
            command['id'] = 'engine:' + str(index)
        terminal = None
        if hero['hp']['current'] <= 0 and not runtime['combat']:
            # r217: a hero the chosen rules would not let die is *out*, not dead,
            # and the way back is the printed stabilizing rule: a long rest.
            out = runtime.get('out') or {}
            if out.get('reason') == 'death':
                terminal = dict(code='native_hero_dead',
                    message='Герой погиб по правилам кампании: риск был объявлен до боя. '
                            'Кампания им не продолжается — откатите ход или восстановите сохранение.')
            elif out:
                terminal = dict(code='native_out_of_fight',
                    message='Герой выбыл из боя и не умер. Длительный отдых возвращает его в строй: '
                            'стабильный персонаж с 0 хитов получает 1 хит через 1d4 часа (SRD 5.2).')
            else:
                terminal = dict(code='native_zero_hp_outside_combat',
                    message='0 HP вне активного боя: восстановление/воскрешение требует отдельной штатной процедуры.')
        # r217: the screen shows the rules the player chose, with what each one
        # changes, so a difficulty is never a hidden number.
        from .nat20_settings import describe, rules_of
        result = dict(hero=hero_view, combat=battle, commands=available, terminal=terminal,
                      settings=describe(rules_of(runtime)), out=(runtime.get('out') or None),
                      compatibility=report)
        self.cache[key] = result
        while len(self.cache) > 16:
            self.cache.popitem(last=False)
        return deepcopy(result)
