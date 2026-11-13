"""r146: прогон ЭФФЕКТА 57 правил класса со статусом `adjudication_required`.

Задача r146 была поставлена как «54 правила» — по факту в таблице `CATALOG`
(`tests/test_class_coverage_proof.py`) на HEAD 82fc081 таких правил **57**
(54 — это число правил со статусом `blocked`, отсюда путаница в карточке).

Метод этого файла — не «интент не отвергнут», а **дельта состояния**:
каждый сторож строит живой бой через `orchestrator.start_combat`, отправляет
`use_feature`/атаку/проверку и смотрит, ЧТО изменилось в проекции
(`LiveCombatView`, `Combatant`, `check.CheckResult`). Сторож падает, если
дельта исчезла.

Два вида сторожей, чтобы не выдавать одно за другое:

* `effect` — правило действительно что-то меняет; в докстроке названо, ЧТО
  именно (было → стало) и где это в движке;
* `hole` — вторая половина правила отсутствует; сторож фиксирует ТОЧНОЕ
  наблюдаемое поведение (причина отказа, пустой список `changes`, флаг
  `False`) и в докстроке называет файл:строку вендора, где кончается
  реализация. Такой сторож намеренно упадёт, когда дыру закроют, — это
  сигнал пересмотреть статус, а не поломка.

Правила, где эффект доказан лишь частично, остаются в статусе
`adjudication_required`: `test_class_coverage_proof.py` требует пустой
`blockers` у `engine_subset_verified`, а дыра у них реальная и названа.

Источники поведения (Linux-пути этого дерева):

* `vendor/nat20/packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical/features/*.json`
  — типизированные активности умений (вендор; правка запрещена карточкой);
* `.venv/lib/python3.14/site-packages/dnd5e_engine/orchestrator.py` — ядро
  (`feature_not_executable` — 10824; умения, известные ядру по имени, — 7521-7530,
  9455-9463, 10917);
* `.../dnd5e_engine/activities/actor_stats.py:61` — `check_modifier`, боевой путь
  проверок;
* `.../dnd5e_engine/check.py:78,274` — автономный разрешатель проверок, который
  умеет и `jack_of_all_trades`, и `reliable_talent`;
* `.../dnd5e_engine/rules/character.py:212` — предел характеристик 20.
"""
from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine import check as C
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance, derive_sheet
from dnd5e_engine.lib_loader import get_lib_loader
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from solo.nat20_canonical import PhbSubclassLoader

import test_class_coverage_proof as R131

ROOT = Path(__file__).resolve().parent.parent
CHAPTER = ROOT / "rules_data/v2/chapters/phb-03-classes"
LOADER = PhbSubclassLoader()
SCORES = R131.SCORES
LOW_DEX = AbilityScores(strength=8, dexterity=17, constitution=13,
                        intelligence=10, wisdom=10, charisma=8)


# --------------------------------------------------------------------------
# Стенд: бой, интент, снимок проекции
# --------------------------------------------------------------------------
def build_member(cls, level, equipment=("longsword",), entity="pc1", zone="0,0",
                 initiative: int | None = 20, hp=200, scores=None, choices=()):
    spec = CharacterBuildSpec(species_slug="human", background_slug="soldier", class_slug=cls,
                              classes={cls: level}, level=level, ability_scores=scores or SCORES,
                              equipment=tuple(equipment), selected_choices=tuple(choices))
    return build_party_member(spec, CombatInstance(entity_id=entity, name=entity.upper(),
                                                   zone_id=zone, initiative=initiative,
                                                   hp_max=hp, hp_current=hp), loader=LOADER)


def combat(cls, level, equipment=("longsword",), ally=False, seed=5, scores=None):
    party = [build_member(cls, level, equipment=equipment, scores=scores)]
    if ally:
        party.append(build_member("fighter", 1, entity="pc2", zone="1,1", initiative=18))
    handle = asyncio.run(O.start_combat(
        session_id="r146-effect", party=party,
        encounter=[EncounterMemberSpec(entity_id="m1", name="Foe", entity_type="Monster",
                                       initiative=1, hp_current=500, hp_max=500, ac=12,
                                       zone_id="1,0")],
        grid_scene=GridScene(width=9, height=7), rng_seed=seed)).handle
    O.drain_pending_events(handle)
    return handle


def describe(event):
    """Имя события + значащие поля (причина отказа, число, бросок) — для отчётов."""
    return type(event).__name__


def send(handle, entity="pc1", **intent):
    """Один интент и имена его событий. Отказ движка приходит исключением — ловим."""
    try:
        asyncio.run(O.submit_player_intent(handle, entity, O.PlayerIntent(**intent)))
    except Exception as exc:                                  # noqa: BLE001 - отказ движка
        return ["Rejected:" + type(exc).__name__ + ":" + str(exc)[:120]]
    return [describe(e) for e in O.drain_pending_events(handle)]


def send_full(handle, entity="pc1", **intent):
    """То же, но с самими событиями (для чтения эффектов и сумм)."""
    try:
        asyncio.run(O.submit_player_intent(handle, entity, O.PlayerIntent(**intent)))
    except Exception as exc:                                  # noqa: BLE001
        return ["Rejected:" + type(exc).__name__ + ":" + str(exc)[:120]], []
    events = O.drain_pending_events(handle)
    return [describe(e) for e in events], events


def reason_of(events, name):
    """Причина отказа из события (первая найденная), иначе None."""
    for row in payload(events, name):
        if row.get("reason"):
            return row["reason"]
    return None


def payload(events, name):
    return [e.model_dump() for e in events if type(e).__name__ == name]


def snapshot(handle):
    """Проекция состояния, по которой видно дельту правила."""
    live = O.get_live(handle)
    actor = live.initiative[live.current_turn_index] if live.initiative else None
    return {
        "hp": dict(live.tracked_hp),
        "temp_hp": dict(live.tracked_temp_hp),
        "conditions": {k: sorted(v) for k, v in live.active_conditions.items() if v},
        "counters": {k: {c: dict(u) for c, u in v.items()} for k, v in live.custom_counters_by_entity.items() if v},
        "slots": {k: dict(v) for k, v in live.spell_slots_by_entity.items()},
        "pact": {k: dict(v) for k, v in live.pact_slots_by_entity.items()},
        "summons": sorted(live.summons),
        "transformations": sorted(live.transformations),
        "attacks_remaining": live.turn.attacks_remaining,
        "dodging": getattr(actor, "dodging", None),
        "flurry_strikes": getattr(actor, "flurry_strikes_remaining", None),
        "disengaging": getattr(actor, "disengaging_this_turn", None),
        "movement": getattr(actor, "movement_remaining", None),
    }


def delta(before, after):
    return {key: (before[key], after[key]) for key in before if before[key] != after[key]}


def feature(slug):
    return get_lib_loader().get_feature(slug)


def activity_id(slug, name):
    acts = {a.name: a.id for a in feature(slug).activities}
    assert name in acts, (slug, sorted(acts))
    return acts[name]


def spent(handle, entity="pc1", counter=""):
    return O.get_live(handle).custom_counters_by_entity.get(entity, {}).get(counter, {}).get("spent", 0)


# --------------------------------------------------------------------------
# Сторожа: доказанные эффекты и названные дыры
# --------------------------------------------------------------------------
def effect_bardic_inspiration():
    """Кость вдохновения: выдана → потрачена → добавлена к броску атаки.

    Было → стало (seed 4): natural 8 + мод 3 = 11 против КЗ 12 (промах без кости),
    `roll_total` 16 — кость к8 дала +5; `EffectExpired(reason='expended')`;
    счётчик `feature_use:bardic-inspiration` 0 → 1.
    Движок: `orchestrator.py:10917` (`_BARDIC_INSPIRATION`, `_granted_die`,
    `_redeemed_die`) — единственный путь применения кости: промах атаки.
    """
    found = []
    for seed in range(8):
        handle = combat("bard", 5, equipment=("dagger",), ally=True, seed=seed)
        grant = send(handle, intent_type="use_feature", feature_id="bardic-inspiration", target_id="pc2")
        assert "EffectApplied" in grant, grant
        assert spent(handle, counter="feature_use:bardic-inspiration") == 1
        send(handle, intent_type="pass")
        names, events = send_full(handle, entity="pc2", intent_type="attack", target_id="m1",
                                  weapon_id="longsword",
                                  redeem_granted_die="feature_grant:bardic-inspiration")
        rolls = payload(events, "AttackRolled")
        if rolls and rolls[0]["roll_total"] > rolls[0]["natural"] + rolls[0]["modifier"]:
            found.append((seed, rolls[0]["natural"], rolls[0]["modifier"], rolls[0]["roll_total"]))
            assert "EffectExpired" in names, names
            break
    assert found, "ни на одном зерне кость не добавлена к броску атаки"
    assert found[0][3] - (found[0][1] + found[0][2]) in range(1, 13), found


def effect_jack_of_all_trades():
    """Мастер на все руки: половина бонуса владения в разрешателе проверок.

    Было → стало (`check.resolve_check`, prof 3): модификатор 1 → 2 на навыке,
    которым персонаж не владеет (половина владения 3//2 = 1).
    Дыра названа отдельным сторожем `hole_jack_of_all_trades_in_combat`.
    """
    common = dict(kind="skill", skill="history",
                  ability_scores=dict(strength=10, dexterity=10, constitution=10,
                                      intelligence=12, wisdom=10, charisma=10),
                  proficient_skills=[], proficient_saves=[], proficiency_bonus=3,
                  rng=random.Random(11))
    plain = C.resolve_check(C.CheckSpec(**common))
    half = C.resolve_check(C.CheckSpec(jack_of_all_trades=True, **common))
    assert plain.modifier == 1, plain
    assert half.modifier == plain.modifier + 1, (plain.modifier, half.modifier)
    assert half.is_proficient is False, half


def effect_reliable_talent():
    """Надёжный талант: бросок ниже 10 считается десяткой.

    Было → стало (seed 2): natural_roll 2 → 10, итог 5 → 13 на навыке со владением.
    Движок: `check.py:78,274` (`CheckSpec.reliable_talent` → `skill_check`).
    """
    common = dict(kind="skill", skill="stealth",
                  ability_scores=dict(strength=10, dexterity=10, constitution=10,
                                      intelligence=12, wisdom=10, charisma=10),
                  proficient_skills=["stealth"], proficient_saves=[], proficiency_bonus=3,
                  rng=random.Random(2))
    plain = C.resolve_check(C.CheckSpec(**common))
    reliable = C.resolve_check(C.CheckSpec(reliable_talent=True, **common))
    assert plain.natural_roll < 10, plain
    assert plain.roll_total == plain.natural_roll + plain.modifier, plain
    assert reliable.natural_roll == 10, reliable
    assert reliable.roll_total == 10 + plain.modifier, reliable


def effect_rage():
    """Ярость: эффект с прибавкой к урону оружия и тратой применения.

    Было → стало: `EffectApplied` с `system.bonuses.mwak.damage = '+2'`;
    счётчик `feature_use:rage` 0 → 1.
    """
    handle = combat("barbarian", 5, equipment=("greataxe",))
    names, events = send_full(handle, intent_type="use_feature", feature_id="rage")
    assert "EffectApplied" in names, names
    changes = {c["key"]: c["value"] for c in payload(events, "EffectApplied")[0]["effect"]["changes"]}
    assert changes.get("system.bonuses.mwak.damage") == "+2", changes
    assert spent(handle, counter="feature_use:rage") == 1


def effect_feral_instinct():
    """Дикий инстинкт: преимущество на бросок инициативы.

    Лист: `initiative_advantage` 0 → 1 на 7 уровне; движок катит инициативу с
    преимуществом, когда она не задана руками (`build_party.py:125` →
    `orchestrator.py:_resolve_initiative`). Было → стало по 12 зёрнам: бросок
    седьмого уровня = max(d1, d2), шестого = d1; ни одно зерно не ухудшилось.
    """
    assert R131.sheet_of("barbarian", 6)["initiative_advantage"] == 0
    assert R131.sheet_of("barbarian", 7)["initiative_advantage"] == 1

    def roll(level, seed):
        handle = asyncio.run(O.start_combat(
            session_id="r146-init", party=[build_member("barbarian", level, initiative=None)],
            encounter=[EncounterMemberSpec(entity_id="m1", name="Foe", entity_type="Monster",
                                           initiative=1, hp_current=500, hp_max=500, ac=12,
                                           zone_id="1,0")],
            grid_scene=GridScene(width=9, height=7), rng_seed=seed)).handle
        O.drain_pending_events(handle)
        return next(c.initiative for c in O.get_live(handle).initiative if c.entity_id == "pc1")

    pairs = [(roll(6, seed), roll(7, seed)) for seed in range(12)]
    assert all(plain <= feral for plain, feral in pairs), pairs
    assert sum(feral > plain for plain, feral in pairs) >= 6, pairs


def effect_martial_arts():
    """Боевые искусства: безоружный удар бьёт костью монаха и Ловкостью.

    Было → стало (те же зёрна, Сила 8 / Ловкость 17): урон монаха 5 уровня
    10, 6, 8, 14 — у воина с теми же характеристиками 0, 0.
    Движок: `orchestrator.py:4488` (`_MARTIAL_ARTS`).
    """
    def damage(cls, seed):
        handle = combat(cls, 5, equipment=(), seed=seed, scores=LOW_DEX)
        names, events = send_full(handle, intent_type="attack", target_id="m1",
                                  weapon_id="unarmed-strike")
        return [e.amount for e in events if type(e).__name__ == "DamageApplied"]

    pairs = [(damage("fighter", seed), damage("monk", seed)) for seed in range(8)]
    hits = [(plain, monk) for plain, monk in pairs if monk]
    assert len(hits) >= 3, pairs
    assert all(monk_damage and monk_damage[0] > (plain[0] if plain else 0)
               for plain, monk_damage in hits), hits


def effect_monks_focus_flurry():
    """Монашеская сосредоточенность: Шквал ударов ставит счётчик ударов и тратит очко.

    Было → стало: `flurry_strikes_remaining` 0 → 2, счётчик
    `feature_use:monks-focus` 0 → 1. Движок: `orchestrator.py:9459`.
    """
    handle = combat("monk", 5, equipment=())
    before = snapshot(handle)
    names, events = send_full(handle, intent_type="use_feature", feature_id="monks-focus",
                              activity_id=activity_id("monks-focus", "Flurry of Blows"))
    assert "IntentSubmitted" in names, names
    after = snapshot(handle)
    assert before["flurry_strikes"] == 0 and after["flurry_strikes"] == 2, delta(before, after)
    assert spent(handle, counter="feature_use:monks-focus") == 1


def effect_flurry_counter():
    """Шквал ударов: счётчик ударов растёт, но атак в бою — столько же.

    Было → стало: `flurry_strikes_remaining` 0 → 2 при счётчике
    `feature_use:monks-focus` 1; третья атака того же хода отвергнута
    `AttackFailed(reason='no_action_economy')` — бюджет атак от Шквала не вырос.
    Дыра названа отдельным сторожем `hole_flurry_extra_attacks`.
    """
    handle = combat("monk", 5, equipment=("longsword",))
    send(handle, intent_type="use_feature", feature_id="monks-focus",
         activity_id=activity_id("monks-focus", "Flurry of Blows"))
    assert snapshot(handle)["flurry_strikes"] == 2
    for _ in range(2):
        names = send(handle, intent_type="attack", target_id="m1", weapon_id="longsword")
        assert "AttackRolled" in names, names
    names, events = send_full(handle, intent_type="attack", target_id="m1", weapon_id="longsword")
    reasons = [e.get("reason") for e in payload(events, "AttackFailed")]
    assert "no_action_economy" in reasons, (names, reasons)
    assert snapshot(handle)["flurry_strikes"] == 2, "счётчик Шквала остался невостребованным"


def effect_deflect_reduce():
    """Отражение атак: снижение урона — реальное событие лечения.

    Было → стало: `use_feature(deflect-attacks/Reduce)` даёт
    `HealingApplied(amount=16)` при полных хитах (движок моделирует снижение
    урона как лечение; реальный банк урона не ведётся — см. докстроку
    `hole_uncanny_dodge_no_halving`).
    """
    handle = combat("monk", 5, equipment=())
    names, events = send_full(handle, intent_type="use_feature", feature_id="deflect-attacks",
                              activity_id=activity_id("deflect-attacks", "Reduce"))
    assert "HealingApplied" in names, names
    assert payload(events, "HealingApplied")[0]["amount"] > 0


def effect_deflect_redirect():
    """Отражение атак: перенаправление даёт бросок спасброска и урон по цели.

    Было → стало: `SaveRolled` по монаху и `DamageApplied` по `m1` (0 урона —
    банк урона пуст). Движок: активность `Redirect` (consumption →
    `monks-focus`), счётчик `feature_use:monks-focus` 0 → 1.
    """
    handle = combat("monk", 5, equipment=())
    names = send(handle, intent_type="use_feature", feature_id="deflect-attacks",
                 activity_id=activity_id("deflect-attacks", "Redirect"), target_id="m1")
    assert "SaveRolled" in names and "DamageApplied" in names, names
    assert spent(handle, counter="feature_use:monks-focus") == 1


def effect_wild_shape():
    """Дикий облик: форма волка встаёт и даёт временные хиты.

    Было → стало: `TempHpApplied` + `transformations['pc1'].form_slug == 'wolf'`.
    Движок: `orchestrator.py:7527`.
    """
    handle = combat("druid", 5, equipment=())
    names, events = send_full(handle, intent_type="use_feature", feature_id="wild-shape",
                              form_id="wolf")
    assert "TempHpApplied" in names, names
    assert O.get_live(handle).transformations["pc1"].form_slug == "wolf"
    assert payload(events, "TempHpApplied")[0]["amount"] > 0


# --------------------------------------------------------------------------
# Дыры: точное наблюдаемое поведение + место, где кончается реализация
# --------------------------------------------------------------------------
def effect_jack_of_all_trades_in_combat():
    """Мастер на все руки: половина владения читается и в БОЕВОМ пути (r177).

    Было (r146, дыра): `activities/actor_stats.py:61` (`check_modifier`) считал
    модификатор только из владения и экспертизы — половины владения там не
    было; `CheckSpec` (единственное место с флагом, `check.py:78`) в бою не
    строился вовсе.
    Стало: `check_modifier` читает `Combatant.jack_of_all_trades` (поле
    заполняет `build_party_member` из листа), proficiency = Бонус владения // 2;
    итог = мод характеристики + половина владения.
    """
    assert R131.sheet_of("bard", 2)["jack_of_all_trades"] is True
    handle = combat("bard", 5, equipment=("dagger",))
    actor = O.get_live(handle).initiative[0]
    mod = O.check_modifier(actor, "int", "history")
    assert mod.proficiency == O.proficiency_bonus_of(actor) // 2, mod
    assert mod.total == mod.ability_mod + mod.proficiency, mod


def hole_font_of_inspiration_no_recharge():
    """Источник вдохновения: применение тратится, кости не возвращаются.

    Было → стало: кость выдана союзнику (счётчик 1) → `use_feature(font-of-inspiration)`
    даёт только `IntentSubmitted`, счётчик остаётся 1. В данных умения одна
    активность «Recharge with Spell Slot» без эффектов:
    `canonical/features/font-of-inspiration.json` (`activities[0].applied_effects` пуст).
    """
    handle = combat("bard", 5, equipment=("dagger",), ally=True)
    send(handle, intent_type="use_feature", feature_id="bardic-inspiration", target_id="pc2")
    assert spent(handle, counter="feature_use:bardic-inspiration") == 1
    names = send(handle, intent_type="use_feature", feature_id="font-of-inspiration")
    assert names == ["IntentSubmitted"], names
    assert spent(handle, counter="feature_use:bardic-inspiration") == 1


def hole_countercharm_no_advantage():
    """Контрочарование: интент принят, преимущества на спасбросок нет.

    Было → стало: только `IntentSubmitted`; состояния/эффекта на актёре не
    появляется (`active_conditions` пуст, `dodging` False).
    Данные: `canonical/features/countercharm.json` — активность utility без
    `applied_effects`; ядро `countercharm` по имени не знает (упоминание только
    в `labels.py`).
    """
    handle = combat("bard", 7, equipment=("dagger",))
    before = snapshot(handle)
    names = send(handle, intent_type="use_feature", feature_id="countercharm")
    assert names == ["IntentSubmitted"], names
    assert delta(before, snapshot(handle)) == {}, "эффект появился — пересмотреть статус"


def hole_ritual_adept_absent():
    """Знаток ритуалов: типизированных активностей нет.

    Было → стало: `CastFailed(reason='feature_not_executable')`.
    Ядро: `orchestrator.py:10824`; данные:
    `canonical/features/ritual-adept.json` — `activities: []`.
    """
    assert feature("ritual-adept").activities == []
    handle = combat("wizard", 5, equipment=("dagger",))
    names, events = send_full(handle, intent_type="use_feature", feature_id="ritual-adept")
    assert "CastFailed" in names, names
    assert reason_of(events, "CastFailed") == "feature_not_executable", names


def hole_epic_boon_has_no_benefit():
    """Эпический дар (12 классов): не в репертуаре и без выгод.

    Было → стало: `CastFailed(reason='not_in_repertoire')` — дар не входит в
    репертуар ни одного класса (`_granted_feature_slugs`), а данных выгод у
    него нет: `canonical/features/epic-boon.json` — `activities: []`,
    `passive_effects: []`. Ядро: слово `epic` встречается только в
    `labels.py:1608`; запрета «не ниже 19 уровня» в ядре тоже нет —
    утверждение прежнего `blockers` прогоном не подтверждается.
    """
    boon = feature("epic-boon")
    assert boon.activities == [] and boon.passive_effects == []
    handle = combat("fighter", 20, equipment=("longsword",))
    names, events = send_full(handle, intent_type="use_feature", feature_id="epic-boon")
    assert "CastFailed" in names, names
    assert reason_of(events, "CastFailed") in {"not_in_repertoire", "feature_not_executable"}, names


def effect_relentless_rage_hold():
    """Непреклонная ярость: падение до 0 под Яростью даёт спасбросок и хиты (r177).

    Было (r146, дыра): спасбросок бросался только вручную, а перехода
    «0 хитов → хиты» не существовало — движок не вёл триггер падения, и
    `Death` наступал как обычно.
    Стало: `orchestrator._relentless_rage_hold` вызывается из
    `_emit_apply_damage` (единственное место, где урон становится хитами):
    спасбросок Тел Сл 10 (+5 за применение), на успехе хиты = 2 × уровень
    Варвара, применение тратится. Полный прогон боя — в
    `tests/test_vendor_five_feature_gaps.py`.
    """
    from test_vendor_five_feature_gaps import (
        test_relentless_rage_holds_at_zero_hit_points as proof,
    )

    proof()


def hole_persistent_rage_no_regain():
    """Непрерывная ярость: применение тратится, Ярость не возвращается.

    Было → стало: `feature_use:rage` = 1 → 1, `feature_use:persistent-rage` 0 → 1,
    счётчика `feature_use:persistent_rage_regained` не появляется.
    Ядро: `orchestrator.py:7522,9451` — константы есть, восстановления по
    событию боя прогон не наблюдает.
    """
    handle = combat("barbarian", 15, equipment=("greataxe",))
    send(handle, intent_type="use_feature", feature_id="rage")
    names = send(handle, intent_type="use_feature", feature_id="persistent-rage")
    assert names == ["IntentSubmitted"], names
    live = O.get_live(handle)
    counters = live.custom_counters_by_entity["pc1"]
    assert counters["feature_use:rage"]["spent"] == 1, counters
    assert "feature_use:persistent_rage_regained" not in counters, counters


def hole_tactical_mind_no_bonus():
    """Тактическое мышление: тратит Второе дыхание, добавки к проверке нет.

    Было → стало: `feature_use:second-wind` 0 → 1 (перекрёстная трата), событий
    эффекта нет; `check_modifier` добавки не видит.
    Данные: `canonical/features/tactical-mind.json` — одна utility-активность
    без `applied_effects`.
    """
    handle = combat("fighter", 5, equipment=("longsword",))
    names = send(handle, intent_type="use_feature", feature_id="tactical-mind")
    assert names == ["IntentSubmitted"], names
    assert spent(handle, counter="feature_use:second-wind") == 1


def hole_indomitable_no_reroll():
    """Упорный: применение тратится, переброса спасброска в движке нет.

    Было → стало: `feature_use:indomitable` 0 → 1, событий кроме
    `IntentSubmitted` нет. В движке нет ни одного пути переброса:
    `grep -rn reroll dnd5e_engine/*.py` даёт только комментарии в
    `activities/save.py`.
    """
    handle = combat("fighter", 9, equipment=("longsword",))
    names = send(handle, intent_type="use_feature", feature_id="indomitable")
    assert names == ["IntentSubmitted"], names
    assert spent(handle, counter="feature_use:indomitable") == 1


def hole_arcane_recovery_no_slots():
    """Магическое восстановление: применение тратится, ячейки не возвращаются.

    Было → стало: `feature_use:arcane-recovery` 0 → 1, `spell_slots_by_entity`
    не меняется. `rest.resolve_short_rest` (rest.py) не принимает ни ячеек, ни
    `arcane_recovery` — восстановления на отдыхе нет.
    """
    handle = combat("wizard", 5, equipment=("dagger",))
    before = snapshot(handle)
    names = send(handle, intent_type="use_feature", feature_id="arcane-recovery")
    assert names == ["IntentSubmitted"], names
    after = snapshot(handle)
    assert after["slots"] == before["slots"], delta(before, after)
    assert spent(handle, counter="feature_use:arcane-recovery") == 1


def hole_spell_mastery_no_slotless_cast():
    """Мастерство заклинателя: применение тратится, заклинание не кастуется.

    Было → стало: только `IntentSubmitted`; `slotless_casts` актёра и
    `spell_slots_by_entity` не меняются.
    """
    handle = combat("wizard", 18, equipment=("dagger",))
    actor = O.get_live(handle).initiative[0]
    before_slotless = dict(getattr(actor, "slotless_casts", {}) or {})
    names = send(handle, intent_type="use_feature", feature_id="spell-mastery")
    assert names == ["IntentSubmitted"], names
    actor = O.get_live(handle).initiative[0]
    assert dict(getattr(actor, "slotless_casts", {}) or {}) == before_slotless


def hole_signature_spells_no_cast():
    """Фирменные заклинания: три активности, все — только интент.

    Было → стало: каждая из трёх активностей даёт ровно `IntentSubmitted`;
    подготовленные заклинания и ячейки не меняются.
    """
    acts = [a.id for a in feature("signature-spells").activities]
    assert len(acts) == 3, acts
    for aid in acts:
        handle = combat("wizard", 20, equipment=("dagger",))
        names = send(handle, intent_type="use_feature", feature_id="signature-spells",
                     activity_id=aid)
        assert names == ["IntentSubmitted"], (aid, names)


def hole_wild_companion_no_summon():
    """Дикий спутник: применение Дикого облика тратится, призыв не садится.

    Было → стало: `feature_use:wild-shape` 0 → 1, `summons` остаётся пустым:
    активность призыва не в белом списке призывов ядра.
    """
    handle = combat("druid", 5, equipment=())
    names = send(handle, intent_type="use_feature", feature_id="wild-companion",
                 activity_id=activity_id("wild-companion", "Summon with Wild Shape"))
    assert "IntentSubmitted" in names, names
    assert O.get_live(handle).summons == {}
    assert spent(handle, counter="feature_use:wild-shape") == 1


def hole_wild_resurgence_no_exchange():
    """Дикое восстановление: тратит форму, но ячейку на форму не меняет.

    Было → стало: `feature_use:wild-shape` 0 → 1, `spell_slots_by_entity`
    не меняется, `transformations` пуст.
    """
    handle = combat("druid", 5, equipment=())
    before = snapshot(handle)
    names = send(handle, intent_type="use_feature", feature_id="wild-resurgence",
                 activity_id=activity_id("wild-resurgence", "Regain Spell Slot"))
    assert "IntentSubmitted" in names, names
    after = snapshot(handle)
    assert after["slots"] == before["slots"], delta(before, after)
    assert after["transformations"] == [] and spent(handle, counter="feature_use:wild-shape") == 1


def hole_archdruid_no_infinite_uses():
    """Архидруид: применения Дикого облика не становятся безлимитными.

    Было → стало: `feature_use:wild-shape` 0 → 1 и продолжает расти; лимита
    «без применения» в движке нет.
    """
    handle = combat("druid", 20, equipment=())
    send(handle, intent_type="use_feature", feature_id="archdruid",
         activity_id=activity_id("archdruid", "Nature Magician"))
    assert spent(handle, counter="feature_use:wild-shape") == 1
    names, events = send_full(handle, intent_type="use_feature", feature_id="archdruid",
                              activity_id=activity_id("archdruid", "Nature Magician"))
    assert "CastFailed" in names, names
    assert spent(handle, counter="feature_use:wild-shape") == 1


def effect_turn_undead():
    """Изгнание нежити: провал спасброска вешает Испуганный и Ошеломлённый.

    Было → стало (seed 1-3): `SaveRolled` → `EffectApplied` → два
    `ConditionApplied`; `active_conditions['m1']` = {frightened, incapacitated}.
    Дыра (проверяется отдельным сторожем `hole_turn_undead_any_creature`):
    тип существа не сверяется — умение сработало по цели `entity_type='Monster'`
    без признака нежити.
    """
    applied = []
    for seed in range(4):
        handle = combat("cleric", 5, equipment=("mace",), seed=seed)
        names = send(handle, intent_type="use_feature", feature_id="channel-divinity-cleric",
                     activity_id=activity_id("channel-divinity-cleric", "Turn Undead"),
                     target_id="m1")
        assert "SaveRolled" in names, names
        applied.append(O.get_live(handle).active_conditions.get("m1") or set())
    assert any({"frightened", "incapacitated"} <= conds for conds in applied), applied


def hole_turn_undead_any_creature():
    """Изгнание нежити: тип цели не проверяется — работает по любому монстру.

    Было → стало: цель объявлена `entity_type='Monster'` (не нежить),
    `creature_type` у неё не задан, а состояния всё равно встают
    (`frightened`, `incapacitated`). В движке нет проверки типа существа для
    этой активности.
    """
    handle = combat("cleric", 5, equipment=("mace",), seed=2)
    send(handle, intent_type="use_feature", feature_id="channel-divinity-cleric",
         activity_id=activity_id("channel-divinity-cleric", "Turn Undead"), target_id="m1")
    foe = next(c for c in O.get_live(handle).initiative if c.entity_id == "m1")
    assert getattr(foe, "creature_type", None) in (None, ""), foe.creature_type
    assert {"frightened", "incapacitated"} <= (O.get_live(handle).active_conditions.get("m1") or set())


def effect_stunning_strike():
    """Ошеломляющий удар: провал спасброска вешает Ошеломлённого, фокус тратится.

    Было → стало (seed 1-4): `feature_use:monks-focus` 0 → 1, состояние
    `stunned` на цели; при успешном спасброске (seed 0, 5) состояния нет.
    """
    seen = []
    for seed in range(6):
        handle = combat("monk", 5, equipment=(), seed=seed)
        names = send(handle, intent_type="use_feature", feature_id="stunning-strike", target_id="m1")
        assert "SaveRolled" in names, names
        assert spent(handle, counter="feature_use:monks-focus") == 1
        seen.append(O.get_live(handle).active_conditions.get("m1") or set())
    assert any("stunned" in conds for conds in seen), seen
    assert any("stunned" not in conds for conds in seen), seen


def effect_abjure_frightened():
    """Порицание врагов: провал спасброска вешает Испуганного.

    Было → стало (seed 1-4): `SaveRolled` → `EffectApplied` →
    `ConditionApplied`; `active_conditions['m1']` содержит `frightened`;
    при успехе (seed 0, 5) состояния нет.
    Дыра (отдельный сторож `hole_abjure_not_charged`): применение Канала не
    списывается со счётчика ресурса.
    """
    seen = []
    for seed in range(6):
        handle = combat("paladin", 9, equipment=("longsword",), seed=seed)
        names = send(handle, intent_type="use_feature", feature_id="abjure-foes", target_id="m1")
        assert "SaveRolled" in names, names
        seen.append(O.get_live(handle).active_conditions.get("m1") or set())
    assert any("frightened" in conds for conds in seen), seen
    assert any("frightened" not in conds for conds in seen), seen


def hole_abjure_not_charged():
    """Порицание врагов: Канал божественности не списывается.

    Было → стало: `use_feature(abjure-foes)` на паладине 9 уровня даёт эффект,
    но `custom_counters_by_entity` пуст (`feature_use:channel-divinity-paladin`
    не появляется вовсе), хотя в данных активность объявляет
    `consumption.targets[0] = feat:channel-divinity-paladin, value 1`.
    """
    handle = combat("paladin", 9, equipment=("longsword",), seed=2)
    names = send(handle, intent_type="use_feature", feature_id="abjure-foes", target_id="m1")
    assert "ConditionApplied" in names, names
    assert spent(handle, counter="feature_use:channel-divinity-paladin") == 0
    assert O.get_live(handle).custom_counters_by_entity.get("pc1", {}) == {}


def hole_divine_intervention_no_cast():
    """Божественное вмешательство: применение тратится, заклинание не идёт.

    Было → стало: `feature_use:divine-intervention` 0 → 1, кроме
    `IntentSubmitted` событий нет (ни `CastStarted`, ни `SpellCast`).
    """
    handle = combat("cleric", 10, equipment=("mace",))
    names = send(handle, intent_type="use_feature", feature_id="divine-intervention")
    assert names == ["IntentSubmitted"], names
    assert spent(handle, counter="feature_use:divine-intervention") == 1


def hole_greater_divine_intervention_no_choice():
    """Великое божественное вмешательство: тратит чужое применение, выбора нет.

    Было → стало: `feature_use:divine-intervention` 0 → 1 (перекрёстная трата),
    выбора заклинания и каста нет.
    """
    handle = combat("cleric", 20, equipment=("mace",))
    names = send(handle, intent_type="use_feature", feature_id="greater-divine-intervention")
    assert names == ["IntentSubmitted"], names
    assert spent(handle, counter="feature_use:divine-intervention") == 1


def hole_magical_cunning_no_pact_slots():
    """Магическая изворотливость: применение тратится, ячейки договора на месте.

    Было → стало: `feature_use:magical-cunning` 0 → 1, `pact_slots_by_entity`
    остаётся `{3: 2}` — восстановления нет.
    """
    handle = combat("warlock", 5, equipment=("dagger",))
    before = snapshot(handle)
    names = send(handle, intent_type="use_feature", feature_id="magical-cunning")
    assert names == ["IntentSubmitted"], names
    after = snapshot(handle)
    assert after["pact"] == before["pact"] and after["pact"] != {}, delta(before, after)


def hole_eldritch_master_no_restore():
    """Таинственный мастер: тратит применения изворотливости, ячейки те же.

    Было → стало: `feature_use:magical-cunning` 0 → 1, `pact_bytes` не меняются.
    """
    handle = combat("warlock", 20, equipment=("dagger",))
    before = snapshot(handle)
    names = send(handle, intent_type="use_feature", feature_id="eldritch-master")
    assert names == ["IntentSubmitted"], names
    after = snapshot(handle)
    assert after["pact"] == before["pact"], delta(before, after)
    assert spent(handle, counter="feature_use:magical-cunning") == 1


def hole_patient_defense_no_dodge():
    """Терпеливая оборона: эффект с состоянием «dodging» применён, флаг не встал.

    Было → стало: `EffectApplied` с `statuses=['dodging']`, но
    `Combatant.dodging` остаётся False — проекция состояния не читает статус
    эффекта. Движок: активность «Patient Defense (Focus Point)» в
    `monks-focus`, эффект `UYHRNxrU74xiUJAy`.
    """
    handle = combat("monk", 5, equipment=())
    names, events = send_full(handle, intent_type="use_feature", feature_id="monks-focus",
                              activity_id=activity_id("monks-focus", "Patient Defense (Focus Point)"))
    assert "EffectApplied" in names, names
    effect = payload(events, "EffectApplied")[0]["effect"]
    assert effect["statuses"] == ["dodging"], effect
    assert O.get_live(handle).initiative[0].dodging is False


def hole_step_of_wind_no_dash():
    """Поступь ветра: эффект «Disengaged» применён, Рывка/перемещения нет.

    Было → стало: `EffectApplied` c пустыми `statuses`, `disengaging_this_turn`
    False, `movement_remaining` 30 → 30.
    """
    handle = combat("monk", 5, equipment=())
    before = snapshot(handle)
    names = send(handle, intent_type="use_feature", feature_id="monks-focus",
                 activity_id=activity_id("monks-focus", "Step of the Wind"))
    assert "EffectApplied" in names, names
    after = snapshot(handle)
    assert after["disengaging"] is False and after["movement"] == before["movement"], delta(before, after)


def hole_flurry_extra_attacks():
    """Шквал ударов: счётчик стоит, лишних атак нет.

    Было → стало: после Шквала `flurry_strikes_remaining` = 2, но бюджет атак
    тот же: вторая атака проходит, третья отвергнута
    `AttackFailed(reason='no_action_economy')`. Счётчик ударов никем не читается
    в пути атаки.
    """
    handle = combat("monk", 5, equipment=("longsword",))
    send(handle, intent_type="use_feature", feature_id="monks-focus",
         activity_id=activity_id("monks-focus", "Flurry of Blows"))
    assert snapshot(handle)["flurry_strikes"] == 2
    for _ in range(2):
        send(handle, intent_type="attack", target_id="m1", weapon_id="longsword")
    names, events = send_full(handle, intent_type="attack", target_id="m1", weapon_id="longsword")
    reasons = [e.get("reason") for e in payload(events, "AttackFailed")]
    assert "no_action_economy" in reasons, (names, reasons)


def hole_disciplined_survivor_no_cleanse():
    """Обученный выживать: применение тратится, снятия состояний нет.

    Было → стало: `feature_use:monks-focus` 0 → 1, `active_conditions` не меняются.
    """
    handle = combat("monk", 14, equipment=())
    before = snapshot(handle)
    names = send(handle, intent_type="use_feature", feature_id="disciplined-survivor")
    assert names == ["IntentSubmitted"], names
    after = snapshot(handle)
    assert after["conditions"] == before["conditions"], delta(before, after)
    assert spent(handle, counter="feature_use:monks-focus") == 1


def hole_body_and_mind_cap_20():
    """Тело и разум: до 25 характеристика не растёт — общий предел 20.

    Было → стало: `derive_sheet` с ASI на 4/8/12/16 отвергает четвёртую
    прибавку: `ValueError: ... these increases stop at 20`
    (`dnd5e_engine/rules/character.py:212`); у умения нет активностей
    (`canonical/features/body-and-mind.json` — `activities: []`).
    """
    assert feature("body-and-mind").activities == []
    with pytest.raises(ValueError, match="stop at 20"):
        R131.sheet_of("monk", 20, choices=("asi:monk:4:dexterity+2", "asi:monk:8:dexterity+2",
                                          "asi:monk:12:dexterity+2", "asi:monk:16:dexterity+2"))


def hole_primal_champion_cap_20():
    """Дикий чемпион: до 25 Сила не растёт — тот же предел 20.

    Было → стало: на 20 уровне с четырьмя ASI `derive_sheet` бросает
    `ValueError: ... these increases stop at 20` (`rules/character.py:212`);
    у умения нет активностей и пассивных эффектов.
    """
    assert feature("primal-champion").activities == []
    with pytest.raises(ValueError, match="stop at 20"):
        R131.sheet_of("barbarian", 20, choices=("asi:barbarian:4:strength+2",
                                               "asi:barbarian:8:strength+2",
                                               "asi:barbarian:12:strength+2",
                                               "asi:barbarian:16:strength+2"))


def guard_reliable_talent_sheet():
    """Надёжный талант: флаг на листе стоит уже с 7 уровня (проверено прогоном).

    `reliable_talent` True на 7 — то есть данные ведут себя по правилу
    PHB 2024; прежнее утверждение «флаг ставится не раньше 11» прогоном не
    подтверждается. Здесь остаётся положительная проверка листа.
    """
    assert R131.sheet_of("rogue", 7)["reliable_talent"] is True


def hole_reliable_talent_not_in_combat():
    """Надёжный талант: в бою путь проверок флаг не берёт.

    Было → стало: `check_modifier` возвращает модификатор без пола 10
    (`activities/actor_stats.py:61`): ни `reliable_talent`, ни `CheckSpec` в
    боевом пути не участвуют.
    """
    handle = combat("rogue", 11, equipment=("rapier",))
    actor = O.get_live(handle).initiative[0]
    mod = O.check_modifier(actor, "dex", "stealth")
    assert not hasattr(mod, "reliable_talent"), mod
    assert mod.total == mod.ability_mod + mod.proficiency, mod


def hole_uncanny_dodge_no_halving():
    """Невероятное уклонение: реакция принимается, урон не половинтся.

    Было → стало: только `IntentSubmitted`; события `DamageApplied` с
    уменьшенным уроном нет, банка урона ядро не ведёт (снижение урона
    моделируется активностью heal, а не множителем 0.5).
    """
    handle = combat("rogue", 5, equipment=("rapier",))
    names = send(handle, intent_type="use_feature", feature_id="uncanny-dodge")
    assert names == ["IntentSubmitted"], names


def hole_stroke_of_luck_no_reroll():
    """Улыбка фортуны: применение тратится, замены броска на 20 нет.

    Было → стало: `feature_use:stroke-of-luck` 0 → 1, других событий нет;
    пути переброса/подмены d20 в движке нет.
    """
    handle = combat("rogue", 20, equipment=("rapier",))
    names = send(handle, intent_type="use_feature", feature_id="stroke-of-luck")
    assert names == ["IntentSubmitted"], names
    assert spent(handle, counter="feature_use:stroke-of-luck") == 1


def hole_aura_of_protection_owner_only():
    """Аура защиты: применение ничего не даёт, пассивный эффект — только владельцу.

    Было → стало: `use_feature(aura-of-protection)` → только `IntentSubmitted`;
    лист паладина не несёт поля добавки к спасброскам
    (`save_bonus` в `derive_sheet` отсутствует), а пассивный эффект
    `Protected` объявлен с `transfer=True` — это эффект владельца, не аура для
    союзников. Данные: `canonical/features/aura-of-protection.json`.
    """
    handle = combat("paladin", 6, equipment=("longsword",), ally=True)
    before = snapshot(handle)
    names = send(handle, intent_type="use_feature", feature_id="aura-of-protection")
    assert names == ["IntentSubmitted"], names
    assert delta(before, snapshot(handle)) == {}
    passive = feature("aura-of-protection").passive_effects[0]
    assert passive.transfer is True
    assert "save_bonus" not in R131.sheet_of("paladin", 6)


def hole_channel_divinity_empty_effect():
    """Проведение божественности (паладин): единственный эффект пуст.

    Было → стало: `EffectApplied` с `changes == []` и `statuses == []`,
    счётчик `feature_use:channel-divinity-paladin` 0 → 1 — то есть применение
    потрачено, а менять нечего: в данных активция «Божественное чувство» без
    изменений (`canonical/features/channel-divinity-paladin.json`).
    """
    handle = combat("paladin", 3, equipment=("longsword",))
    names, events = send_full(handle, intent_type="use_feature",
                              feature_id="channel-divinity-paladin")
    assert "EffectApplied" in names, names
    effect = payload(events, "EffectApplied")[0]["effect"]
    assert effect["changes"] == [] and effect["statuses"] == [], effect
    assert spent(handle, counter="feature_use:channel-divinity-paladin") == 1


def hole_restoring_touch_no_cleanse():
    """Восстанавливающее прикосновение: тратит пул рук, состояние не снимает.

    Было → стало: `feature_use:lay-on-hands` 0 → 5 (пул), `active_conditions`
    цели не меняются.
    """
    handle = combat("paladin", 14, equipment=("longsword",))
    before = snapshot(handle)
    names = send(handle, intent_type="use_feature", feature_id="restoring-touch", target_id="m1")
    assert "IntentSubmitted" in names, names
    after = snapshot(handle)
    assert after["conditions"] == before["conditions"], delta(before, after)
    assert spent(handle, counter="feature_use:lay-on-hands") == 5


def hole_font_of_magic_no_conversion():
    """Источник магии: обе активности тратят применения, обмена нет.

    Было → стало: `feature_use:font-of-magic` 0 → 1 на каждой активности;
    `spell_slots_by_entity` не меняется ни в одну сторону.
    """
    for name in ("Regain Spell Slot", "Regain Sorcery Points"):
        handle = combat("sorcerer", 5, equipment=("dagger",))
        before = snapshot(handle)
        names = send(handle, intent_type="use_feature", feature_id="font-of-magic",
                     activity_id=activity_id("font-of-magic", name))
        assert names == ["IntentSubmitted"], (name, names)
        after = snapshot(handle)
        assert after["slots"] == before["slots"], (name, delta(before, after))
        assert spent(handle, counter="feature_use:font-of-magic") == 1


def hole_sorcery_incarnate_no_restore():
    """Воплощение чародейства: тратит 2 очка, Врождённое чародейство не возвращает.

    Было → стало: `feature_use:font-of-magic` 0 → 2, счётчик
    `feature_use:innate-sorcery` не появляется/не уменьшается.
    """
    handle = combat("sorcerer", 7, equipment=("dagger",))
    names = send(handle, intent_type="use_feature", feature_id="sorcery-incarnate")
    assert names == ["IntentSubmitted"], names
    counters = O.get_live(handle).custom_counters_by_entity["pc1"]
    assert counters["feature_use:font-of-magic"]["spent"] == 2, counters
    assert "feature_use:innate-sorcery" not in counters, counters


def hole_bard_subclass_feature_absent(slug, label):
    """Подклассовое умение барда: типизированного умения в библиотеке нет.

    Было → стало: `get_feature(slug) is None` — движок отвечает
    `feature_not_in_repertoire`; в библиотеке 12 подклассов, ни одного из
    коллегий барда (доблести/очарования/танца) нет.
    """
    assert feature(slug) is None, slug
    assert get_lib_loader().list_slugs("subclasses") == [
        "berserker", "champion", "devotion", "draconic", "evoker", "fiend",
        "hand", "hunter", "land", "life", "lore", "thief"], label


# --------------------------------------------------------------------------
# Таблица: класс, ключ правила, вид сторожа, что именно проверяется
# --------------------------------------------------------------------------
RULE_GUARDS = [
    ("bard", "vardo_vdohnovenie", "effect", effect_bardic_inspiration),
    ("bard", "master_na_vse_ruki", "effect", effect_jack_of_all_trades),
    ("bard", "master_na_vse_ruki", "effect", effect_jack_of_all_trades_in_combat),
    ("bard", "istochnik_vdohnoveniya", "hole", hole_font_of_inspiration_no_recharge),
    ("bard", "kontrocharovanie", "hole", hole_countercharm_no_advantage),
    ("bard", "epicheskii_dar", "hole", hole_epic_boon_has_no_benefit),
    ("bard", "kolegiya_dobletsi_boevoe_vdohnovenie", "hole",
     lambda: hole_bard_subclass_feature_absent("combat-inspiration", "доблести")),
    ("bard", "kolegiya_ocharovaniya_maniya_vdohnoveniya", "hole",
     lambda: hole_bard_subclass_feature_absent("mantle-of-inspiration", "очарования")),
    ("bard", "kolegiya_tantsa_oslepitelnye_fuete", "hole",
     lambda: hole_bard_subclass_feature_absent("dazzling-footwork", "танца")),

    ("barbarian", "yarost", "effect", effect_rage),
    ("barbarian", "dikii_instinkt", "effect", effect_feral_instinct),
    ("barbarian", "nepreklonnaya_yarost", "effect", effect_relentless_rage_hold),
    ("barbarian", "nepreryvnaya_yarost", "hole", hole_persistent_rage_no_regain),
    ("barbarian", "dikii_chempion", "hole", hole_primal_champion_cap_20),
    ("barbarian", "epicheskii_dar", "hole", hole_epic_boon_has_no_benefit),

    ("fighter", "takticheskoe_myshlenie", "hole", hole_tactical_mind_no_bonus),
    ("fighter", "upornyi", "hole", hole_indomitable_no_reroll),
    ("fighter", "epicheskii_dar", "hole", hole_epic_boon_has_no_benefit),

    ("wizard", "znatok_ritualov", "hole", hole_ritual_adept_absent),
    ("wizard", "magicheskoe_vosstanovlenie", "hole", hole_arcane_recovery_no_slots),
    ("wizard", "masterstvo_zaklinatelya", "hole", hole_spell_mastery_no_slotless_cast),
    ("wizard", "firmennye_zaklinaniya", "hole", hole_signature_spells_no_cast),
    ("wizard", "epicheskii_dar", "hole", hole_epic_boon_has_no_benefit),

    ("druid", "dikii_oblik", "effect", effect_wild_shape),
    ("druid", "dikii_sputnik", "hole", hole_wild_companion_no_summon),
    ("druid", "dikoe_vosstanovlenie", "hole", hole_wild_resurgence_no_exchange),
    ("druid", "arhidruid", "hole", hole_archdruid_no_infinite_uses),
    ("druid", "epicheskii_dar", "hole", hole_epic_boon_has_no_benefit),

    ("cleric", "izgnanie_nezheti", "effect", effect_turn_undead),
    ("cleric", "izgnanie_nezheti", "hole", hole_turn_undead_any_creature),
    ("cleric", "bozhestvennoe_vmeshatelstvo", "hole", hole_divine_intervention_no_cast),
    ("cleric", "velikoe_bozhestvennoe_vmeshatelstvo", "hole",
     hole_greater_divine_intervention_no_choice),
    ("cleric", "epicheskii_dar", "hole", hole_epic_boon_has_no_benefit),

    ("warlock", "magicheskaya_izvorotlivost", "hole", hole_magical_cunning_no_pact_slots),
    ("warlock", "tainstvennyi_master", "hole", hole_eldritch_master_no_restore),
    ("warlock", "epicheskii_dar", "hole", hole_epic_boon_has_no_benefit),

    ("monk", "boevye_iskusstva", "effect", effect_martial_arts),
    ("monk", "monasheskaya_sosredotochennost", "effect", effect_monks_focus_flurry),
    ("monk", "shkval_udarov", "effect", effect_flurry_counter),
    ("monk", "shkval_udarov", "hole", hole_flurry_extra_attacks),
    ("monk", "terpelivaya_oborona", "hole", hole_patient_defense_no_dodge),
    ("monk", "postup_vetra", "hole", hole_step_of_wind_no_dash),
    ("monk", "otrazhenie_atak", "effect", effect_deflect_reduce),
    ("monk", "otrazhenie_atak", "effect", effect_deflect_redirect),
    ("monk", "oshelomlyayushii_udar", "effect", effect_stunning_strike),
    ("monk", "obuchennyi_vyzhivat", "hole", hole_disciplined_survivor_no_cleanse),
    ("monk", "telo_i_razum", "hole", hole_body_and_mind_cap_20),
    ("monk", "epicheskii_dar", "hole", hole_epic_boon_has_no_benefit),

    ("paladin", "provedenie_bozhestvennosti", "hole", hole_channel_divinity_empty_effect),
    ("paladin", "aura_zashity", "hole", hole_aura_of_protection_owner_only),
    ("paladin", "poritsanie_vragov", "effect", effect_abjure_frightened),
    ("paladin", "poritsanie_vragov", "hole", hole_abjure_not_charged),
    ("paladin", "vosstanavlivayushee_prikosnovenie", "hole", hole_restoring_touch_no_cleanse),
    ("paladin", "epicheskii_dar", "hole", hole_epic_boon_has_no_benefit),

    ("rogue", "neveroyatnoe_uklonenie", "hole", hole_uncanny_dodge_no_halving),
    ("rogue", "nadezhnyi_talant", "effect", effect_reliable_talent),
    ("rogue", "nadezhnyi_talant", "guard", guard_reliable_talent_sheet),
    ("rogue", "nadezhnyi_talant", "hole", hole_reliable_talent_not_in_combat),
    ("rogue", "ulybka_fortuny", "hole", hole_stroke_of_luck_no_reroll),
    ("rogue", "epicheskii_dar", "hole", hole_epic_boon_has_no_benefit),

    ("ranger", "epicheskii_dar", "hole", hole_epic_boon_has_no_benefit),

    ("sorcerer", "istochnik_magii", "hole", hole_font_of_magic_no_conversion),
    ("sorcerer", "voploshenie_charodeistva", "hole", hole_sorcery_incarnate_no_restore),
    ("sorcerer", "epicheskii_dar", "hole", hole_epic_boon_has_no_benefit),
]

ADJUDICATION = sorted(key for key, row in R131.CATALOG.items()
                      if row["status"] == "adjudication_required")

#: Плоские доказательства эффекта для карты `CATALOG` (вид `("r146", ключ)`):
#: правило считается исполняемым, только пока этот прогон зелёный.
R146_PROOFS = {
    "barbarian.dikii_instinkt": effect_feral_instinct,
}


def test_every_adjudication_rule_has_a_guard():
    """Ни одно правило `adjudication_required` не осталось без прогона."""
    covered = {(cls, key) for cls, key, _kind, _fn in RULE_GUARDS
               if (cls, key) in R131.CATALOG
               and R131.CATALOG[(cls, key)]["status"] == "adjudication_required"}
    missing = set(ADJUDICATION) - covered
    assert not missing, missing
    unknown = {(cls, key) for cls, key, _k, _f in RULE_GUARDS} - set(R131.CATALOG)
    assert not unknown, unknown


def test_adjudication_map_and_corpus_agree():
    """Ключи таблицы совпадают с картой корпуса (тот же инвариант, что в r131)."""
    rows = {}
    for path in sorted(CHAPTER.glob("*.json")):
        slug = R131.SLUG_BY_FILE[path.name]
        for row in json.loads(path.read_text(encoding="utf-8"))["rules"]:
            rows[(slug, row["id"].split(".")[3])] = row["readiness"]["status"]
    for cls, key, _kind, _fn in RULE_GUARDS:
        assert rows[(cls, key)] == R131.CATALOG[(cls, key)]["status"], (cls, key)


@pytest.mark.parametrize("cls,key,kind,guard", RULE_GUARDS,
                         ids=[f"{cls}-{key}-{kind}" for cls, key, kind, _ in RULE_GUARDS])
def test_rule_effect_or_hole(cls, key, kind, guard):
    assert kind in {"effect", "hole", "guard"}
    guard()


def test_counts_read_back():
    """Счёт по выборке: сколько правил доказано эффектом, сколько осталось с дырой."""
    rules = {(cls, key) for cls, key, _kind, _fn in RULE_GUARDS}
    effects = {(cls, key) for cls, key, kind, _fn in RULE_GUARDS if kind == "effect"}
    holes = {(cls, key) for cls, key, kind, _fn in RULE_GUARDS if kind == "hole"}
    assert set(ADJUDICATION) <= rules, set(ADJUDICATION) - rules
    assert effects, "ни одного доказанного эффекта"
    assert holes, "ни одной названной дыры"
    still = set(ADJUDICATION) & effects
    print(f"\nr146: правил в выборке {len(rules)}, с доказанным эффектом {len(effects)}, "
          f"с названной дырой {len(holes)}; из них остаются adjudication_required, "
          f"хотя эффект доказан: {sorted(still)}")
