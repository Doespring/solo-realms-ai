"""PHB 2024, «Урон и лечение»: состояния, временные хиты, стабилизация, нокаут.

Источник чисел — PHB 2024 RU (`runtime/books/Player's Handbook 2024 RUS.pdf`,
глава 1): «Опускание Хитов до 0», «Спасброски от смерти», «Стабилизация
персонажа», «Временные хиты», «Нокаутирование существа» (печатные стр. 28–30,
дословный текст разобран в `rules_data/v2/evidence/phb-008-033.txt`).
Определения состояний — глоссарий правил; те же числа уже посчитаны вендором
(`dnd5e_engine/rules/conditions.py`), поэтому таблица ниже — хостовое зеркало
одного набора правил, а не второй движок.

Правило здесь — данные, а не проза: каждый флаг таблицы имеет число из книги,
и бой читает флаги вместо зашитых в код наборов строк. Где движок считает не
всё (проверки «требующие слуха», независимые длительности одного состояния),
это записано в `LIMITS`, а не выдано за готовое.
"""
from collections.abc import Iterable
from dataclasses import dataclass, field

#: Сл проверки Мудрости (Медицина) для стабилизации и первой помощи.
STABILIZATION_DC = 10
#: Часы, через которые стабилизированное существо без лечения получает 1 Хит.
STABILIZED_DIE = (1, 4)


@dataclass(frozen=True)
class ConditionEffect:
    """Одно состояние с числами правила.

    ``advantages_attacks_against`` — атака ПО носителю идёт с преимуществом;
    ``own_attacks_disadvantage`` — собственные атаки носителя идут с помехой;
    ``auto_crit_within_5ft`` — попадание в упор становится критическим;
    ``auto_fail_saves``/``save_disadvantage`` — спасброски по характеристикам;
    ``blocks_actions`` — нельзя действие, бонусное действие и реакцию;
    ``speed_zero`` — скорость 0, ``implies`` — состояния, которые правило
    включает неявно (Парализован включает Недееспособность и т.д.).
    """

    id: str
    name: str
    advantages_attacks_against: bool = False
    own_attacks_disadvantage: bool = False
    auto_crit_within_5ft: bool = False
    auto_fail_saves: tuple[str, ...] = ()
    save_disadvantage: tuple[str, ...] = ()
    check_disadvantage: bool = False
    blocks_actions: bool = False
    speed_zero: bool = False
    cannot_attack_source: bool = False
    implies: tuple[str, ...] = ()
    note: str = ''


#: Глоссарий правил, 14 состояний из `Hero.conditions`. Истощение — отдельное
#: число (`Hero.exhaustion`), его помеха и скорость считает RulesetEngine.
CONDITIONS: dict[str, ConditionEffect] = {c.id: c for c in (
    ConditionEffect('blinded', 'Ослеплён', advantages_attacks_against=True, own_attacks_disadvantage=True,
                    note='Автопровал проверок, требующих зрения, движок не считает: у проверок нет признака «требует зрения».'),
    ConditionEffect('charmed', 'Очарован', cannot_attack_source=True,
                    note='Запрет атаковать источник и преимущество источника на социальные проверки.'),
    ConditionEffect('deafened', 'Оглох', note='Проверки на слух движок не считает: у проверок нет признака «требует слуха».'),
    ConditionEffect('frightened', 'Напуган', own_attacks_disadvantage=True, check_disadvantage=True,
                    note='Помеха только пока источник страха в поле зрения; отход к источнику запрещён.'),
    ConditionEffect('grappled', 'Схвачен', speed_zero=True),
    ConditionEffect('incapacitated', 'Недееспособен', blocks_actions=True, speed_zero=True),
    ConditionEffect('invisible', 'Невидим', note='Видимость считает геометрия боя (`CombatEngine.sees`), не эта таблица.'),
    ConditionEffect('paralyzed', 'Парализован', advantages_attacks_against=True, auto_crit_within_5ft=True,
                    auto_fail_saves=('strength', 'dexterity'), blocks_actions=True, speed_zero=True,
                    implies=('incapacitated',)),
    ConditionEffect('petrified', 'Окаменел', advantages_attacks_against=True,
                    auto_fail_saves=('strength', 'dexterity'), blocks_actions=True, speed_zero=True,
                    implies=('incapacitated',),
                    note='Сопротивление всему урону и иммунитет к Отравленному — в `CombatEngine.damage`.'),
    ConditionEffect('poisoned', 'Отравлен', own_attacks_disadvantage=True, check_disadvantage=True),
    ConditionEffect('prone', 'Сбит с ног', own_attacks_disadvantage=True,
                    note='По носителю в пределах 5 футов — преимущество, дальше — помеха. Спасброски Сбитый с ног не меняет.'),
    ConditionEffect('restrained', 'Опутан', advantages_attacks_against=True, own_attacks_disadvantage=True,
                    save_disadvantage=('dexterity',), speed_zero=True),
    ConditionEffect('stunned', 'Ошеломлён', advantages_attacks_against=True,
                    auto_fail_saves=('strength', 'dexterity'), blocks_actions=True, speed_zero=True,
                    implies=('incapacitated',)),
    ConditionEffect('unconscious', 'Без сознания', advantages_attacks_against=True, auto_crit_within_5ft=True,
                    auto_fail_saves=('strength', 'dexterity'), blocks_actions=True, speed_zero=True,
                    implies=('incapacitated', 'prone')),
)}

#: Чего таблица не считает. Печатается в отчёт движка, чтобы «состояния готовы»
#: не звучало шире правды.
LIMITS = (
    'Проверки «требующие зрения/слуха» (Ослеплён, Оглох) не различаются: у проверки нет такого признака.',
    'Независимые длительности одного состояния не хранятся: движок помнит одну длительность на состояние.',
    'Один бросок урона на несколько целей (область) не исполняется: нет исполнителя массовых эффектов.',
)


def expand(names: Iterable[str]) -> set[str]:
    """Состояния вместе с теми, что правило включает неявно."""
    found = {str(name) for name in names}
    changed = True
    while changed:
        changed = False
        for name in tuple(found):
            effect = CONDITIONS.get(name)
            if effect is None:
                continue
            for implied in effect.implies:
                if implied not in found:
                    found.add(implied)
                    changed = True
    return found


def known(names: Iterable[str]) -> list[str]:
    """Неизвестные имена состояний — ошибка данных, а не «просто текст»."""
    return sorted({str(n) for n in names if str(n) not in CONDITIONS})


def attack_adjustments(*, attacker: Iterable[str] = (), target: Iterable[str] = (),
                       distance_ft: int = 5, target_dodging: bool = False,
                       attacker_can_see_target: bool = True, defender_sees_attacker: bool = True,
                       fear_source_in_sight: bool = True,
                       target_speed_positive: bool = True) -> dict:
    """Преимущество и помеха одного броска атаки по таблице состояний.

    `defender_sees_attacker=False` — нападающего не видно, преимущество;
    `attacker_can_see_target=False` — цель не видна, помеха.
    Возвращает причины по-русски: они идут в запись броска и на экран игрока.
    """
    mine = {str(n) for n in attacker}
    theirs = {str(n) for n in target}
    adv: list[str] = []
    dis: list[str] = []
    if not defender_sees_attacker:
        adv.append('нападающего не видно')
    if any(CONDITIONS[name].advantages_attacks_against for name in theirs if name in CONDITIONS):
        adv.append('цель не может защищаться')
    # Сбитый с ног: преимущество в упор, помеха издалека (PHB 2024, глоссарий).
    if 'prone' in theirs:
        adv.append('цель сбита с ног') if distance_ft <= 5 else dis.append('цель сбита с ног вдалеке')
    if 'blinded' in mine:
        dis.append('нападающий ослеплён')
    if 'restrained' in mine:
        dis.append('нападающий опутан')
    if 'prone' in mine:
        dis.append('нападающий сбит с ног')
    if 'poisoned' in mine:
        dis.append('отравление')
    if 'frightened' in mine and fear_source_in_sight:
        dis.append('испуг')
    if target_dodging and target_speed_positive:
        dis.append('уклонение цели')
    if not attacker_can_see_target:
        dis.append('цель не видна')
    return dict(advantage=adv, disadvantage=dis)


def save_rule(conditions: Iterable[str], ability: str) -> dict:
    """Автопровал и помеха спасброска по характеристике.

    PHB 2024, глоссарий: Парализован/Ошеломлён/Окаменел/Без сознания —
    провал спасбросков Силы и Ловкости без броска; Опутан — помеха Ловкости.
    Сбитый с ног спасброски не меняет: автопровал Ловкости приходит только из
    перечисленных состояний, и подменять одно другим нельзя.
    """
    names = expand(conditions)
    auto = sorted({ability for name in names if name in CONDITIONS
                   for ability in CONDITIONS[name].auto_fail_saves})
    disadvantage = sorted({ability for name in names if name in CONDITIONS
                           for ability in CONDITIONS[name].save_disadvantage})
    reason = ''
    if ability in auto:
        reason = 'провал спасброска без броска: ' + ', '.join(
            CONDITIONS[name].name for name in sorted(names)
            if name in CONDITIONS and ability in CONDITIONS[name].auto_fail_saves)
    elif ability in disadvantage:
        reason = 'помеха на спасбросок: ' + ', '.join(
            CONDITIONS[name].name for name in sorted(names)
            if name in CONDITIONS and ability in CONDITIONS[name].save_disadvantage)
    return dict(auto_fail=ability in auto, disadvantage=ability in disadvantage, reason=reason)


def auto_crit(conditions: Iterable[str], distance_ft: int) -> bool:
    """Попадание в упор по Парализованному/Бессознательному — критическое."""
    if distance_ft > 5:
        return False
    return any(CONDITIONS[name].auto_crit_within_5ft
               for name in expand(conditions) if name in CONDITIONS)


def blocks_actions(conditions: Iterable[str]) -> bool:
    return any(CONDITIONS[name].blocks_actions for name in expand(conditions) if name in CONDITIONS)


def speed_is_zero(conditions: Iterable[str]) -> bool:
    return any(CONDITIONS[name].speed_zero for name in expand(conditions) if name in CONDITIONS)


def check_disadvantage(conditions: Iterable[str], *, fear_source_in_sight: bool = True) -> list[str]:
    """Помеха на проверки характеристик с причиной."""
    reasons = []
    for name in expand(conditions):
        effect = CONDITIONS.get(name)
        if effect is None or not effect.check_disadvantage:
            continue
        if name == 'frightened' and not fear_source_in_sight:
            continue
        reasons.append(effect.name)
    return reasons


def bloodied(hp: int, max_hp: int) -> bool:
    """«Если у вас половина Хитов и меньше, вы становитесь Окровавленным»."""
    return hp > 0 and hp * 2 <= max_hp


def temp_hp_grant(existing: int, incoming: int, *, keep: str = 'existing') -> dict:
    """Временные хиты: не складываются, игрок выбирает старые или новые.

    PHB 2024, печ. стр. 29: «если заклинание даёт вам 12 Временных хитов, когда
    у вас уже есть 10, у вас может быть или 12, или 10, но не 22». Автоматически
    оставлять большее нельзя — это выбор игрока, поэтому `keep` обязателен, а
    при отсутствии старых хитов выбор не нужен.
    """
    if incoming <= 0:
        raise ValueError('Новых Временных хитов нет: прибавка должна быть положительной')
    if keep not in ('existing', 'new'):
        raise ValueError('Выберите, какие Временные хиты оставить: старые или новые')
    if existing <= 0:
        return dict(value=incoming, kept='new', discarded=0, stack_forbidden=existing + incoming,
                    text='Временные хиты: %d' % incoming)
    value = incoming if keep == 'new' else existing
    discarded = existing if keep == 'new' else incoming
    return dict(value=value, kept=keep, discarded=discarded, stack_forbidden=existing + incoming,
                text='Временные хиты: %d (выбраны %s, %d отброшено; сложение запрещено)'
                     % (value, 'новые' if keep == 'new' else 'старые', discarded))


def knockout_applies(*, requested: bool, ranged: bool, hp_before: int, amount: int) -> bool:
    """Нокаут вместо смерти: рукопашная атака, которая опустила бы Хиты до 0."""
    return bool(requested and not ranged and hp_before > 0 and amount >= hp_before)


def knockout_state() -> dict:
    """Хиты после нокаута и состояние, которое он накладывает."""
    return dict(knockout=True, hp=1, condition='unconscious', begins='short_rest',
                text='Нокаут: Хиты 1 и Бессознательность, начинается Короткий отдых')


def stabilized_hours(roll: int) -> int:
    """Сколько часов до возврата 1 Хита стабилизированному без лечения."""
    low, high = STABILIZED_DIE
    return max(1, min(high, int(roll)))


__all__ = [
    'CONDITIONS', 'LIMITS', 'STABILIZATION_DC', 'STABILIZED_DIE', 'ConditionEffect',
    'attack_adjustments', 'auto_crit', 'bloodied', 'blocks_actions', 'check_disadvantage',
    'expand', 'known', 'knockout_applies', 'knockout_state', 'save_rule', 'speed_is_zero',
    'stabilized_hours', 'temp_hp_grant',
]
