"""One formatter for what a resolved check says to the player.

Two roll paths hand the host dicts of different shape: ``RulesetEngine.check``
returns ``total``/``bonus``/``dc``/``mode``, the Nat20 worker returns
``roll_total``/``modifier``/``dc``/``natural_roll``. The player must read the
same sentence either way, so ``command_engine``, ``narrative_grounding`` and the
client all take the line from here instead of writing their own phrase.

Numbers are copied out of the result dict, never recomputed: a wrong bonus in
the engine must stay visible instead of being papered over by the formatter.
A record with no numbers (an old campaign stores the bare word "успех") keeps a
neutral phrase. That phrase is spelled in this module and nowhere else.
"""
from .rules import ABILITIES
from .rulesets import SKILL_NAMES

SUCCESS = 'успех'
FAILURE = 'неудача'
NO_RECORD = 'Результат проверки записан без чисел.'
_FALLBACK = {True: 'Действие удалось.', False: 'Попытка не удалась.'}
# Nat20 names abilities by their three-letter score key, the host by full name.
_SHORT_ABILITY = {'str': 'strength', 'dex': 'dexterity', 'con': 'constitution',
                  'int': 'intelligence', 'wis': 'wisdom', 'cha': 'charisma'}
_SAVE_KINDS = ('save', 'saving_throw')
# 'Спасбросок Мудрости', not 'Спасбросок Мудрость': the check name is a
# genitive phrase, and the nominative table above cannot be reused for it.
_GENITIVE = {'Сила': 'Силы', 'Ловкость': 'Ловкости', 'Телосложение': 'Телосложения',
             'Интеллект': 'Интеллекта', 'Мудрость': 'Мудрости', 'Харизма': 'Харизмы'}
_MODES = ('преимущество', 'помеха')
_EFFECT_WORDS = {'advantage': 'преимущество', 'disadvantage': 'помеха'}


def _int(value):
    """An integer field, or None. ``True`` is not a number here."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ability_name(value):
    key = str(value or '').strip().casefold()
    return ABILITIES.get(_SHORT_ABILITY.get(key, key))


def _name(result):
    """Russian check name: the skill when the record has one, else the ability.

    An unknown slug is never printed as-is: a table says 'Атлетика', not
    'athletics'. Unknown skill plus unknown ability leaves the generic word.
    """
    skill = str(result.get('skill') or '').strip().casefold()
    if skill in SKILL_NAMES:
        return SKILL_NAMES[skill]
    ability = _ability_name(result.get('ability'))
    kind = str(result.get('type') or result.get('kind') or '')
    if kind in _SAVE_KINDS:
        return 'Спасбросок ' + _GENITIVE.get(ability, ability) if ability else ''
    return ability or ''


def _markers(result):
    """Named circumstances: natural die first, then advantage or disadvantage."""
    marks = []
    mode = str(result.get('mode') or '')
    natural = _int(result.get('natural_roll'))
    if natural is None:
        kept = _int(result.get('selected'))
        dice = result.get('dice')
        # Only a kept die can be natural: with disadvantage the engine keeps the
        # lower roll, so a discarded 20 is not a natural 20.
        if kept is not None and isinstance(dice, (list, tuple)) and dice:
            natural = kept
    if (natural == 20 and mode != 'помеха') or (natural == 1 and mode != 'преимущество'):
        marks.append('натуральная ' + str(natural))
    if mode in _MODES:
        marks.append(mode)
    else:
        # Nat20 reports circumstances as breakdown lines instead of a mode word.
        breakdown = {str(item) for item in result.get('effect_breakdown') or ()}
        marks.extend(word for key, word in _EFFECT_WORDS.items() if 'effect(' + key + ')' in breakdown)
    return marks


def check_text(result):
    """A resolved check as one Russian sentence, or the neutral fallback.

    >>> check_text(dict(skill='stealth', total=17, dc=15, success=True))
    'Скрытность: 17 против Сл 15 — успех'
    """
    if not isinstance(result, dict):
        return NO_RECORD
    success = result.get('success')
    if not isinstance(success, bool):
        return NO_RECORD
    total = _int(result.get('total'))
    if total is None:
        total = _int(result.get('roll_total'))
    dc = _int(result.get('dc'))
    if total is None or dc is None:
        # No roll survived (old campaign): a phrase, not invented numbers.
        return _FALLBACK[success]
    marks = _markers(result)
    note = ' (' + ', '.join(marks) + ')' if marks else ''
    return '%s: %d против Сл %d — %s%s' % (_name(result) or 'Проверка', total, dc,
                                           SUCCESS if success else FAILURE, note)


# --- Attacks -------------------------------------------------------------
# Battle text is spelled here for the same reason as checks: the chat line, the
# stream and the combat journal must not each invent their own sentence.
ATTACK_HIT = 'попадание'
ATTACK_MISS = 'промах'
_ATTACK_FALLBACK = {True: 'Атака: попадание.', False: 'Атака: промах.'}
WRONG_LOCATION = 'Атака: промах — цели не было там, куда били'


def _attack_marks(roll, result):
    """Named circumstances: natural die, the mode with its reason, cover, a crit."""
    marks = []
    mode = str(roll.get('mode') or '')
    selected = _int(roll.get('selected'))
    if (selected == 20 and mode != 'помеха') or (selected == 1 and mode != 'преимущество'):
        marks.append('натуральная ' + str(selected))
    advantage = [str(item) for item in roll.get('advantage_reasons') or ()]
    disadvantage = [str(item) for item in roll.get('disadvantage_reasons') or ()]
    if mode in _MODES:
        reasons = disadvantage if mode == 'помеха' else advantage
        marks.append(mode + (': ' + ', '.join(reasons) if reasons else ''))
    elif advantage and disadvantage:
        # One advantage and one disadvantage cancel out (2024 rules); say so
        # instead of printing a plain roll that looks unexplained.
        marks.append('преимущество и помеха отменены')
    cover = _int(result.get('cover_bonus')) or 0
    if cover:
        marks.append('укрытие +%d' % cover)
    # R173: переброс вдохновением виден игроку как «до и после», а не только
    # внутри записи броска. Кость обязана быть использована — новой.
    spent = roll.get('inspiration') or result.get('inspiration') or {}
    if isinstance(spent, dict) and spent.get('spent'):
        marks.append('вдохновение: переброшено %s → %s' % (spent.get('before'), spent.get('after')))
    if result.get('ability_reason'):
        marks.append('характеристика: %s' % result.get('ability_reason'))
    if result.get('critical'):
        marks.append('критическое попадание')
    return marks


def attack_text(result):
    """A resolved attack as one Russian sentence, or the neutral fallback.

    >>> attack_text(dict(hit=False, target_ac=12, cover_bonus=0,
    ...                  roll=dict(selected=5, bonus=5, total=10, mode='помеха',
    ...                             disadvantage_reasons=['рядом враг'])))
    'Атака: d20 5 +5 = 10 против КЗ 12 — промах (помеха: рядом враг)'
    """
    if not isinstance(result, dict):
        return NO_RECORD
    if result.get('wrong_location'):
        return WRONG_LOCATION
    hit = result.get('hit')
    if not isinstance(hit, bool):
        return NO_RECORD
    roll = result.get('roll')
    if not isinstance(roll, dict):
        return _ATTACK_FALLBACK[hit]
    total = _int(roll.get('total'))
    ac = _int(result.get('target_ac'))
    if total is None or ac is None:
        return _ATTACK_FALLBACK[hit]
    cover = _int(result.get('cover_bonus')) or 0
    bonus = _int(roll.get('bonus'))
    selected = _int(roll.get('selected'))
    if selected is None or bonus is None:
        formula = 'Атака: %d против КЗ %d' % (total, ac + cover)
    else:
        formula = 'Атака: d20 %d %+d = %d против КЗ %d' % (selected, bonus, total, ac + cover)
        spent = roll.get('inspiration') or {}
        if isinstance(spent, dict) and spent.get('spent'):
            formula += ' (до переброса d20 %s %+d = %s)' % (spent.get('before'), bonus, spent.get('total_before'))
    marks = _attack_marks(roll, result)
    note = ' (' + ', '.join(marks) + ')' if marks else ''
    return '%s — %s%s' % (formula, ATTACK_HIT if hit else ATTACK_MISS, note)
