"""Campaign rules the player sets (r217): death, difficulty, rewards.

The owner's decision (03.11.2026, ``SPEC-COMPANIONS.md``, пп. 4-5): whether the
character can die, and how hard the game is, are chosen by the player — the
master never turns a number down in silence.

This module holds the *table* of what each choice changes and nothing else. The
numbers are applied by the host where the number is used: the encounter opening
(:mod:`solo.nat20_worker`, ``start_combat``), the ability checks the host rolls
(``check`` / ``scene_action``), and the reward the campaign books when a fight
ends. The narrator is never told "be kinder": it is told what actually changed,
because the change is a fact in the journal.

Three values live in the campaign state (``DurableState.settings``) and are
changed only through ``PUT /api/campaigns/{id}/settings``, which writes one
journal event. Nothing here computes a D&D rule; it prices the host's own
handling of the rules.
"""
from typing import Literal

from pydantic import Field

from .contracts import Strict


class CampaignRules(Strict):
    """The three rules a player owns (r217).

    Defaults are the rules as printed: ``death='agreed'`` because the owner's
    standing decision is that irreversible outcomes are a matter of agreement,
    ``difficulty='as_is'`` and ``rewards='normal'`` because a campaign that
    never asked must not be strengthened or weakened behind the player's back.
    """

    death: Literal['off', 'agreed', 'on'] = 'agreed'
    difficulty: Literal['solo_softer', 'as_is', 'harder'] = 'as_is'
    rewards: Literal['normal', 'sparse', 'generous'] = 'normal'


#: What each difficulty does to the numbers of an encounter and to a check DC.
#: ``hp_percent`` is applied to every standing enemy's maximum and current hit
#: points; ``drop_every_second_foe`` is the solo rule — with one hero at the
#: table every second prepared enemy stays away, so «softer» is fewer foes, not
#: only smaller ones.
DIFFICULTY = {
    'solo_softer': dict(hp_percent=70, attack_bonus=-1, check_dc=-2,
                        drop_every_second_foe=True, label='соло-мягче'),
    'as_is': dict(hp_percent=100, attack_bonus=0, check_dc=0,
                  drop_every_second_foe=False, label='как есть'),
    'harder': dict(hp_percent=150, attack_bonus=1, check_dc=2,
                   drop_every_second_foe=False, label='жёстче'),
}

#: Percent of the experience the engine awarded that the campaign actually books.
REWARDS = {'normal': 100, 'sparse': 50, 'generous': 150, }

DEATH_LABEL = {'off': 'выключена', 'agreed': 'по договорённости', 'on': 'включена'}
REWARDS_LABEL = {'normal': 'обычные', 'sparse': 'скупые', 'generous': 'щедрые'}

DEATH_TEXT = {
    'off': 'Персонаж с 0 хитов выбывает из боя, но не умирает. Кампания '
           'продолжается: после боя герой приходит в себя длительным отдыхом.',
    'agreed': 'Мастер спрашивает перед необратимым исходом. Без заранее '
              'объявленного риска смерть останавливается, механический '
              'результат сохраняется; согласие даётся до боя, а не задним числом.',
    'on': 'Правила как есть: смерть возможна, но только после того, как риск '
          'объявлен до начала столкновения.',
}

DIFFICULTY_TEXT = {
    'solo_softer': 'Врагов меньше (один герой за столом — каждый второй '
                   'подготовленный враг не выходит), их хиты −30%, бонус атаки −1, '
                   'КС проверок −2.',
    'as_is': 'Числа как в правилах: враги, их хиты, бонус атаки и КС проверок '
             'не меняются.',
    'harder': 'Хиты врагов +50%, бонус атаки +1, КС проверок +2. Врагов столько '
              'же, сколько подготовлено.',
}

REWARDS_TEXT = {
    'normal': 'Опыт начисляется так, как его посчитал движок.',
    'sparse': 'Опыт за столкновение — половина посчитанного движком (скупые награды).',
    'generous': 'Опыт за столкновение — полторы порции посчитанного движком.',
}


def rules_of(state):
    """The campaign's rules, tolerantly read from a saved state.

    A campaign saved before r217 has no ``settings`` key at all; it keeps the
    defaults instead of failing to open, and nothing is written back until the
    player changes a setting.
    """
    raw = state.get('settings') if isinstance(state, dict) else None
    if not raw:
        return CampaignRules()
    try:
        return CampaignRules.model_validate(raw)
    except Exception:  # noqa: BLE001 - a corrupt value must not brick a campaign
        return CampaignRules()


def describe(rules=None):
    """The player-facing view of the three settings, with what each changes."""
    rules = rules or CampaignRules()
    return dict(death=rules.death, difficulty=rules.difficulty, rewards=rules.rewards,
                death_label=DEATH_LABEL[rules.death],
                difficulty_label=DIFFICULTY[rules.difficulty]['label'],
                rewards_label=REWARDS_LABEL[rules.rewards],
                death_text=DEATH_TEXT[rules.death],
                difficulty_text=DIFFICULTY_TEXT[rules.difficulty],
                rewards_text=REWARDS_TEXT[rules.rewards],
                effects=dict(
                    monster_hp_percent=DIFFICULTY[rules.difficulty]['hp_percent'],
                    monster_attack_bonus=DIFFICULTY[rules.difficulty]['attack_bonus'],
                    check_dc=DIFFICULTY[rules.difficulty]['check_dc'],
                    fewer_foes=DIFFICULTY[rules.difficulty]['drop_every_second_foe'],
                    reward_percent=REWARDS[rules.rewards]))


def fight_rules(initial, state):
    """The rules a fight was opened with; pre-r217 saves carry none.

    A fight saved before r217 has no terms of its own. Its numbers were already
    fixed in the save, so only the closing reward can be priced: it falls back to
    the campaign's current rules instead of inventing a table.
    """
    raw = (initial or {}).get('settings') if isinstance(initial, dict) else None
    if isinstance(raw, dict):
        try:
            return CampaignRules.model_validate(raw)
        except Exception:  # noqa: BLE001 - a corrupt record must not close a fight
            pass
    return rules_of(state)


def sentence(rules):
    """One journal line: what the rules now are and what they change."""
    return ('Правила кампании: смерть — ' + DEATH_LABEL[rules.death] + '; '
            'сложность — ' + DIFFICULTY[rules.difficulty]['label'] + ' ('
            + DIFFICULTY_TEXT[rules.difficulty] + '); награды — '
            + REWARDS_LABEL[rules.rewards] + ' (' + REWARDS_TEXT[rules.rewards] + ')')


def change_sentence(before, after):
    """The journal line for a settings change: what moved, from what to what.

    Only the parameters that actually changed are named, so a save that writes
    the same three values back does not pretend to have changed anything.
    """
    if before == after:
        return 'Правила кампании не изменились: ' + sentence(after).removeprefix('Правила кампании: ')
    moved = []
    if before.death != after.death:
        moved.append('смерть — ' + DEATH_LABEL[after.death] + ' (было: '
                     + DEATH_LABEL[before.death] + ')')
    if before.difficulty != after.difficulty:
        moved.append('сложность — ' + DIFFICULTY[after.difficulty]['label'] + ' (было: '
                     + DIFFICULTY[before.difficulty]['label'] + ')')
    if before.rewards != after.rewards:
        moved.append('награды — ' + REWARDS_LABEL[after.rewards] + ' (было: '
                     + REWARDS_LABEL[before.rewards] + ')')
    return ('Правила кампании изменены: ' + '; '.join(moved) + '. Теперь они значат: '
            + DIFFICULTY_TEXT[after.difficulty] + ' ' + DEATH_TEXT[after.death] + ' '
            + REWARDS_TEXT[after.rewards])


def scale_encounter(rules, foes, *, party_size=1):
    """The enemies that actually stand, and the line that says what changed.

    ``foes`` is a list of plain dicts with ``name``, ``hp`` and ``attack_bonus``
    (the values the worker is about to hand to the engine). Returns
    ``(kept, notes)``; ``notes`` is empty when the numbers are the printed ones,
    so an unchanged encounter says nothing and the journal stays honest.
    """
    kept = [dict(foe) for foe in foes]
    notes = []
    table = DIFFICULTY[rules.difficulty]
    if rules.difficulty == 'as_is':
        # "Как есть" is a strict no-op: not one number moves, so a clamp here
        # cannot silently turn a printed -1 to-hit into a zero.
        return kept, notes
    if table['drop_every_second_foe'] and int(party_size) <= 1 and len(kept) > 1:
        stand = kept[::2]
        notes.append('Сложность «соло-мягче»: врагов ' + str(len(stand))
                     + ' вместо ' + str(len(kept)) + '.')
        kept = stand
    changed = []
    for foe in kept:
        before = int(foe.get('hp') or 0)
        after = max(0, (before * table['hp_percent'] + 99) // 100)
        foe['hp'] = after
        bonus_before = int(foe.get('attack_bonus') or 0)
        bonus_after = max(-10, min(30, bonus_before + table['attack_bonus']))
        foe['attack_bonus'] = bonus_after
        if after != before:
            changed.append(str(foe.get('name') or foe.get('id') or 'враг')
                           + ': хиты ' + str(before) + ' → ' + str(after))
        if bonus_after != bonus_before:
            changed.append(str(foe.get('name') or foe.get('id') or 'враг')
                           + ': атака +' + str(bonus_before) + ' → +' + str(bonus_after))
    if changed:
        notes.append('Сложность «' + table['label'] + '»: ' + '; '.join(changed) + '.')
    return kept, notes


def check_dc(rules, dc):
    """The DC a host-rolled check uses under these rules (clamped 5..30).

    ``None`` stays ``None``: a check with no printed DC is resolved by the
    engine's own table, and the difficulty has no number here to move.
    """
    if dc is None:
        return None
    shift = DIFFICULTY[rules.difficulty]['check_dc']
    return max(5, min(30, int(dc) + shift))


def check_note(rules, before, after):
    """The journal line for a changed DC, or ``''`` when it is the printed one."""
    if before is None or after is None or int(before) == int(after):
        return ''
    return ('Сложность «' + DIFFICULTY[rules.difficulty]['label'] + '»: КС '
            + str(before) + ' → ' + str(after) + '.')


def reward_amount(rules, amount):
    """Experience the campaign books for an engine-awarded amount."""
    amount = int(amount or 0)
    if amount <= 0:
        return 0
    percent = REWARDS[rules.rewards]
    return max(1, amount * percent // 100)


def reward_note(rules, before, after):
    if int(before) == int(after):
        return ''
    return ('Награды «' + REWARDS_LABEL[rules.rewards] + '»: опыт '
            + str(int(before)) + ' → ' + str(int(after)) + '.')


#: What the host says when an irreversible outcome is stopped. ``agreed`` asks
#: the player; ``on`` without a declared risk says which agreement was missing.
DEATH_QUESTION = {
    'agreed': ('Мастер спрашивает: исход необратим, герой погибнет. Смерть «по '
               'договорённости»: механический результат сохранён, но смерть '
               'остановлена. Если смерть в этой кампании должна быть, смените '
               'правило на «включена» и объявите риск до следующего столкновения '
               '— задним числом согласие не действует.'),
    'on': ('Смерть включена, но риск не был объявлен до начала этого '
           'столкновения. Необратимый исход остановлен, механический результат '
           'сохранён: объявленный риск действует только вперёд.'),
}


__all__ = ['CampaignRules', 'DIFFICULTY', 'REWARDS', 'DEATH_LABEL', 'REWARDS_LABEL',
           'DEATH_TEXT', 'DIFFICULTY_TEXT', 'REWARDS_TEXT', 'DEATH_QUESTION',
           'rules_of', 'fight_rules', 'describe', 'sentence', 'change_sentence', 'scale_encounter',
           'check_dc', 'check_note', 'reward_amount', 'reward_note']
