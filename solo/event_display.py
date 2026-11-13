"""The one place a resolved command becomes a sentence the player can read.

The screen used to parse the engine's own line — ``Nat20 confirmed: check`` — and
keep a second Russian dictionary in the browser, so a new command kind needed a
synchronous edit of the server and the front, a player's or Master's line that
began the same way was silently replaced, and the wording could not change
without rebuilding the front. The server now answers with two fields for every
event it hands out: ``display_text`` (ready to show) and ``display_key`` (a
stable identifier, never meant to be parsed).
"""
import re

# Every kind the command layer can resolve today: the labels of
# solo/playable_ui.py plus the kinds solo/play_screen.py used to translate.
# A missing kind still gets a readable sentence instead of engine English.
KINDS = {
    'check': 'Проверка выполнена.',
    'start_combat': 'Бой начат.',
    'move': 'Перемещение выполнено.',
    'attack': 'Атака выполнена.',
    'end_turn': 'Ход завершён.',
    'end_combat': 'Бой завершён.',
    'short_rest': 'Короткий отдых завершён.',
    'long_rest': 'Длительный отдых завершён.',
    'dash': 'Рывок выполнен.',
    'use_feature': 'Умение применено.',
    'cast_spell': 'Заклинание прочитано.',
    'drop_concentration': 'Концентрация прекращена.',
    'native_action': 'Действие героя подтверждено.',
    'scene_action': 'Действие сцены подтверждено.',
    'order': 'Спутник ответил на просьбу.',
}
UNKNOWN = 'Действие подтверждено движком.'
# The exact line older events stored before this module existed. A multi-part
# outcome ('… / Бой начат.') never matches, so a real sentence is left alone.
_LEGACY = re.compile(r'^Nat20 confirmed:\s*([a-z_]+)$')


def display_text(kind):
    """The Russian sentence for one command kind; readable even for an unknown one."""
    return KINDS.get(str(kind or ''), UNKNOWN)


def translate_legacy(value):
    """A stored 'Nat20 confirmed: kind' line becomes its display sentence.

    Only the whole line matches: the check path has carried numbers since r133
    and its text must reach the player exactly as the engine wrote it.
    """
    if not isinstance(value, str):
        return value
    match = _LEGACY.match(value.strip())
    return display_text(match.group(1)) if match else value


def fields(kind, text=''):
    """The two fields an event carries to the screen."""
    return dict(display_key=str(kind or ''), display_text=translate_legacy(text) or display_text(kind))


def decorate(event):
    """Add the display fields to a dumped engine event without changing it."""
    payload = event.get('payload') if isinstance(event.get('payload'), dict) else {}
    kind = payload.get('command') or event.get('display_key') or ''
    return {**event, **fields(kind, payload.get('outcome') or event.get('text') or '')}


# --- native operations -----------------------------------------------------
#
# r187: игрок читал «Действие героя подтверждено» и не знал, схвачена ли цель,
# промахнулся ли он из-за укрытия и ударила ли его провоцированная атака.
# Движок всё это считает и отдаёт событиями; хост обязан назвать числа, иначе
# правило работает, а игра выглядит как «ничего не произошло».

OP_NAMES = {
    'attack': 'Атака', 'grapple': 'Захват', 'shove': 'Толчок',
    'escape_grapple': 'Освобождение от захвата', 'stand_up': 'Подъём',
    'drop_prone': 'Лечь',
}


def _field(action, key):
    if isinstance(action, dict):
        return action.get(key)
    return getattr(action, key, None)


def _of_type(events, kind):
    return [event for event in events or [] if isinstance(event, dict) and event.get('type') == kind]


def _save_phrase(save):
    if not save or save.get('roll_total') is None:
        return ''
    return 'спасбросок %s против КС %s' % (save.get('roll_total'), save.get('dc'))


def _damage_phrase(events, target_id):
    total = sum(event.get('amount') or 0 for event in _of_type(events, 'damage_applied')
                if target_id in (None, event.get('target_id')))
    return 'урон %d' % total if total else ''


def _cover_phrase(action, events, grid, view):
    """Укрытие — только подпись к ответу движка, числа берутся из его же шва."""
    rolled = _of_type(events, 'attack_rolled')
    if not rolled or not grid or not view:
        return ''
    zones = view.get('actor_zone') or {}
    attacker = rolled[0].get('attacker_id')
    target = rolled[0].get('target_id')
    origin, cell = zones.get(attacker), zones.get(target)
    if not origin or not cell:
        return ''
    occupied = [position for entity, position in zones.items()
                if entity not in (attacker, target) and position]
    from .nat20_content import COVER_NAMES, cover_bonus, cover_degree
    degree = cover_degree(grid, origin, cell, occupied)
    if degree == 'none':
        return ''
    row = next((actor for actor in view.get('initiative') or []
                if actor.get('entity_id') == target), {})
    base = row.get('ac')
    bonus = cover_bonus(degree)
    if base is None:
        return 'укрытие %s (+%d к КЗ)' % (COVER_NAMES.get(degree, degree), bonus)
    return 'укрытие %s (+%d к КЗ: %s → %s)' % (COVER_NAMES.get(degree, degree), bonus, base, base + bonus)


def native_sentence(action, events, *, grid=None, view=None):
    """Одна реплика о том, что движок реально сделал с операцией героя."""
    operation = str(_field(action, 'operation') or '')
    if operation == 'attack':
        rolled = _of_type(events, 'attack_rolled')
        if not rolled:
            return display_text('attack')
        roll = rolled[0]
        provoked = any(event.get('is_opportunity_attack') for event in rolled)
        head = 'Провоцированная атака' if provoked else 'Атака'
        if roll.get('is_crit'):
            outcome = 'критическое попадание'
        elif roll.get('is_hit'):
            outcome = 'попадание'
        else:
            outcome = 'промах'
        parts = ['%s: %s' % (head, outcome)]
        if roll.get('is_hit'):
            damage = _damage_phrase(events, roll.get('target_id'))
            if damage:
                parts.append(damage)
        cover = _cover_phrase(action, events, grid, view)
        if cover:
            parts.append(cover)
        return '; '.join(parts) + '.'
    conditions = [event.get('condition') for event in _of_type(events, 'condition_applied')
                  if event.get('condition')]
    save_event = (_of_type(events, 'save_rolled') or [None])[0]
    save = _save_phrase(save_event)
    failed = bool(save_event) and save_event.get('succeeded') is False
    if operation == 'grapple':
        if 'grappled' in conditions:
            return 'Захват: цель схвачена' + (' (%s)' % save if save else '') + '; из захвата она не может двигаться.'
        return 'Захват не удался: цель устояла' + (' (%s)' % save if save else '') + '.'
    if operation == 'shove':
        if 'prone' in conditions:
            return 'Толчок: цель сбита с ног' + (' (%s)' % save if save else '') + '.'
        if failed and _field(action, 'shove_push'):
            # r194: «отброшена» — утверждение о состоянии боя, и оно верно
            # только когда движок правда сдвинул цель. Замер: толчок
            # отбрасывает в сторону от героя, и у края сетки сдвига нет
            # (`combatant_moved` не приходит), а прежняя строка всё равно
            # говорила «отброшена на 5 футов».
            shifted = any(str(event.get('type', '')).endswith('moved')
                          for event in events or [] if isinstance(event, dict))
            if shifted:
                return 'Толчок: цель отброшена на 5 футов' + (' (%s)' % save if save else '') + '.'
            return ('Толчок: цель не устояла, но отбросить её некуда — она осталась на месте'
                    + (' (%s)' % save if save else '') + '.')
        return 'Толчок не удался: цель устояла' + (' (%s)' % save if save else '') + '.'
    if operation == 'escape_grapple':
        check = (_of_type(events, 'check_rolled') or [None])[0]
        if 'grappled' not in conditions and check and check.get('succeeded'):
            return 'Освобождение: захват снят (проверка %s против КС %s).' % (
                check.get('roll_total'), check.get('dc'))
        if check:
            return 'Освобождение не удалось (проверка %s против КС %s).' % (
                check.get('roll_total'), check.get('dc'))
        return 'Освобождение: захват ещё держит.'
    if operation == 'stand_up':
        return 'Герой встал.' if 'prone' not in conditions else 'Подъём не удался: герой остался сбитым с ног.'
    # r197: движок принимает добровольное «лечь» (SRD 5.2 Prone: состояние без
    # траты действия). Строка называет и цену — встать стоит половину
    # скорости, — потому что иначе игрок не понимает, что натворил.
    if operation == 'drop_prone':
        if 'prone' in conditions:
            return 'Герой лёг: он сбит с ног (атаки по нему с преимуществом; встать — половина скорости).'
        return 'Лечь не удалось: состояние не изменилось.'
    # r194: штатная кампания теперь получает Уклонение и Отход, и они не
    # должны читаться «Действие героя подтверждено». Обе строки — замер, а
    # не вера в пин: атака монстра после `dodge` пришла с
    # `advantage='disadvantage'`, `sources=['dodge']`; уход после
    # `disengage` дал только `actor_moved` (провоцированной атаки нет).
    if operation == 'dodge':
        return 'Уклонение: атаки по герою идут с помехой до начала его следующего хода.'
    if operation == 'disengage':
        return 'Отход: движение героя в этот ход не провоцирует атаки.'
    if operation == 'help':
        others = [member for member in ((view or {}).get('party_ids') or []) if member != 'hero']
        if not others:
            return 'Помощь: союзников в бою нет — грант истекает на своём же ходу.'
        return 'Помощь: союзник получит преимущество на атаке по отвлечённой цели.'
    name = OP_NAMES.get(operation)
    if name and conditions:
        from .damage_conditions import CONDITIONS
        return '%s: %s.' % (name, ', '.join(CONDITIONS[c].name if c in CONDITIONS else c
                                           for c in conditions))
    return display_text('native_action')


def provoked_attacks(events, *, grid=None, view=None):
    """Провоцированные атаки в событиях хода: их мог вызвать ЛЮБОЙ шаг.

    Движок бьёт по уходящему сам и помечает бросок ``is_opportunity_attack``.
    Команда «Переместиться» об этом не говорила, и игрок не знал, что его
    ударили на выходе из зоны досягаемости. r206: подпись получает ту же сетку
    и проекцию боя, что и обычная атака героя, поэтому провоцированная атака
    тоже называет укрытие, поднявшее КЗ.
    """
    rolled = [event for event in _of_type(events, 'attack_rolled')
              if event.get('is_opportunity_attack')]
    if not rolled:
        return ''
    return native_sentence({'operation': 'attack'}, [event for event in events or []
                                                     if event.get('type') in ('attack_rolled', 'damage_applied')],
                           grid=grid, view=view)
