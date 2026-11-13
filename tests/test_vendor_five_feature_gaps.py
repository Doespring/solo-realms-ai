"""r177 — пять дыр ВЕНДОРА, закрытых патчем (интент → событие → состояние).

Каждый сторож проверяет не «интент не отвергнут», а дельту состояния живого
боя: бросок с костью и её трату, отказ с причиной, хиты после падения до нуля,
модификатор проверки, длительность эффекта и перемещение цели.

Правила книги (SRD 5.2 / PHB 2024), закрытые здесь:

* ``bard.vardo_vdohnovenie`` — кость вдохновения теперь берётся заклинательной
  атакой и спасброском, не только атакой оружием;
* ``bard.master_na_vse_ruki`` — боевой путь проверок читает половину владения;
* ``barbarian.yarost`` — Ярость не включается в тяжёлом доспехе, под Яростью
  заклинание отклоняется, концентрация снимается;
* ``barbarian.nepreklonnaya_yarost`` — падение до 0 под Яростью даёт спасбросок
  Тел. Сл 10 (+5 за применение), успех меняет хиты на удвоенный уровень;
* ``cleric.izgnanie_nezheti`` — эффект «Turned» живёт минуту и гонит цель прочь.

Прогон идёт через публичный бой (``orchestrator.start_combat`` /
``submit_player_intent`` / ``advance_monster_turn``), а не через внутренние
функции: доказательство должно переживать рефакторинг.
"""
from __future__ import annotations

import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.lib_loader import get_lib_loader
from dnd5e_engine.specs import EncounterMemberSpec, GridScene

LOADER = get_lib_loader()

SCORES = AbilityScores(strength=16, dexterity=14, constitution=14,
                       intelligence=10, wisdom=16, charisma=16)
# Мультикласс в волшебника требует Интеллект 13.
SMART = AbilityScores(strength=16, dexterity=14, constitution=14,
                      intelligence=14, wisdom=16, charisma=16)


# --------------------------------------------------------------------------
# Стенд
# --------------------------------------------------------------------------
def member(cls, level, equipment=("longsword",), entity="pc1", zone="0,0",
           initiative=20, hp=200, classes=None, scores=None, choices=()):
    spec = CharacterBuildSpec(species_slug="human", background_slug="soldier",
                              class_slug=cls, level=level,
                              ability_scores=scores or SCORES,
                              equipment=tuple(equipment), classes=classes or {},
                              selected_choices=tuple(choices))
    return build_party_member(
        spec,
        CombatInstance(entity_id=entity, name=entity.upper(), zone_id=zone,
                       initiative=initiative, hp_max=hp, hp_current=hp),
        loader=LOADER,
    )


def foe(initiative=1, hp=500, ac=12, zone="1,0", slug=None, creature_type=None,
        entity="m1", name="Foe"):
    return EncounterMemberSpec(entity_id=entity, name=name, entity_type="Monster",
                               initiative=initiative, hp_current=hp, hp_max=hp,
                               ac=ac, zone_id=zone, monster_template_slug=slug,
                               creature_type=creature_type)


def combat(party, *, seed=5, foes=None, width=9, height=7, session="r177"):
    handle = asyncio.run(O.start_combat(
        session_id=session, party=party, encounter=foes or [foe()],
        grid_scene=GridScene(width=width, height=height), rng_seed=seed)).handle
    O.drain_pending_events(handle)
    return handle


def send(handle, entity="pc1", **intent):
    """Один интент: (имена событий, сами события). Отказ движка — как событие."""
    try:
        asyncio.run(O.submit_player_intent(handle, entity, O.PlayerIntent(**intent)))
    except Exception as exc:  # noqa: BLE001 — отказ движка это данные прогона
        return ["Rejected:" + type(exc).__name__ + ":" + str(exc)[:120]], []
    events = O.drain_pending_events(handle)
    return [type(e).__name__ for e in events], events


def monster_turn(handle):
    asyncio.run(O.advance_monster_turn(handle))
    events = O.drain_pending_events(handle)
    return [type(e).__name__ for e in events], events


def rows(events, name):
    return [e.model_dump() for e in events if type(e).__name__ == name]


def reason(events, name):
    return next((row["reason"] for row in rows(events, name) if row.get("reason")), None)


def visible(handle):
    """Проекция состояния, по которой видно дельту правила."""
    live = O._get_live(handle)
    return {
        "hp": dict(live.tracked_hp),
        "conditions": {k: sorted(v) for k, v in live.active_conditions.items() if v},
        "counters": {k: {c: dict(u) for c, u in v.items()}
                     for k, v in live.custom_counters_by_entity.items() if v},
        "effects": {k: [(e.id, e.duration.model_dump()) for e in v]
                    for k, v in live.active_effects.items() if v},
        "zones": dict(live.actor_zone),
    }


def activity_id(feature_slug, name):
    acts = {a.name: a.id for a in LOADER.get_feature(feature_slug).activities}
    assert name in acts, (feature_slug, sorted(acts))
    return acts[name]


# --------------------------------------------------------------------------
# 1. Кость вдохновения: заклинательная атака
# --------------------------------------------------------------------------
def test_bardic_die_turns_a_missed_spell_attack():
    """Кость берётся заклинательной атакой.

    Было (r177 до патча): `AttackRolled.roll_total` = natural + modifier,
    кость не брошена, эффект «Inspired» остаётся у получателя навсегда.
    Стало: на промахе (не натуральная 1) кость к8 добавляется в `roll_total`,
    `EffectExpired(reason='expended')` снимает эффект.
    Движок: `orchestrator._granted_die_failure`/`_granted_die`
    (`_DIE_REDEEMABLE_INTENTS`), `activities/attack.py`.
    """
    found = []
    for seed in range(10):
        handle = combat([member("bard", 5, ("dagger",), "pc1", "0,0", 20),
                         member("warlock", 5, ("dagger",), "pc2", "1,1", 18)],
                        seed=seed, session="r177-spellattack")
        send(handle, intent_type="use_feature", feature_id="bardic-inspiration",
             target_id="pc2")
        send(handle, intent_type="pass")  # ход переходит держателю кости
        names, events = send(handle, entity="pc2", intent_type="cast_spell",
                             spell_id="eldritch-blast", target_id="m1",
                             redeem_granted_die="feature_grant:bardic-inspiration")
        roll = rows(events, "AttackRolled")
        if not roll:
            continue
        row = roll[0]
        if row["roll_total"] > row["natural"] + row["modifier"]:
            found.append((seed, row["natural"], row["modifier"], row["roll_total"]))
            assert "EffectExpired" in names, names
            assert reason(events, "EffectExpired") == "expended", events
            break
    assert found, "ни на одном зерне кость не добавлена к заклинательной атаке"
    seed, natural, modifier, total = found[0]
    assert 1 <= total - (natural + modifier) <= 8, found


# --------------------------------------------------------------------------
# 2. Кость вдохновения: спасбросок
# --------------------------------------------------------------------------
def test_bardic_die_turns_a_failed_saving_throw():
    """Кость берётся спасброском и МОЖЕТ перевернуть провал в успех.

    Было (до патча): провал спасброска Вис от `vicious-mockery` остаётся
    провалом, кость не тронута. Стало: на провале (не натуральная 1) кость
    получателя добавляется к итогу, `EffectExpired(reason='expended')`.
    Движок: `activities/save_primitive._redeem_bardic_inspiration` через
    `ctx.granted_dices`, который заполняет `orchestrator._granted_dice_by_entity`.
    """
    flips = []
    for seed in range(12):
        handle = combat([member("bard", 5, ("dagger",), "pc1", "0,0", 20),
                         member("fighter", 3, ("longsword",), "pc2", "1,1", 18)],
                        seed=seed, session="r177-save")
        send(handle, intent_type="use_feature", feature_id="bardic-inspiration",
             target_id="pc2")
        names, events = send(handle, intent_type="cast_spell",
                             spell_id="vicious-mockery", target_id="pc2")
        save = rows(events, "SaveRolled")
        assert save, (seed, names)
        row = save[0]
        if row["succeeded"] and row["natural"] + row["modifier"] < row["dc"]:
            flips.append((seed, row["natural"], row["modifier"], row["roll_total"], row["dc"]))
            assert "EffectExpired" in names, names
            assert reason(events, "EffectExpired") == "expended", events
            break
    assert flips, "кость не перевернула ни одного провала спасброска"
    seed, natural, modifier, total, dc = flips[0]
    assert total >= dc > natural + modifier, flips


def test_bardic_die_is_not_spent_on_a_successful_save():
    """Успешный спасбросок кость не тратит — правило «when the creature FAILS»."""
    kept = []
    for seed in range(12):
        handle = combat([member("bard", 5, ("dagger",), "pc1", "0,0", 20),
                         member("fighter", 3, ("longsword",), "pc2", "1,1", 18)],
                        seed=seed, session="r177-save-keep")
        send(handle, intent_type="use_feature", feature_id="bardic-inspiration",
             target_id="pc2")
        names, events = send(handle, intent_type="cast_spell",
                             spell_id="vicious-mockery", target_id="pc2")
        save = rows(events, "SaveRolled")
        if save and save[0]["succeeded"]:
            kept.append(seed)
            assert "EffectExpired" not in names, (seed, names)
            assert "effect:inspired" in visible(handle)["effects"].get("pc2", [])[0][0]
            break
    assert kept, "ни одного успешного спасброска на 12 зёрнах — сторож не проверен"


# --------------------------------------------------------------------------
# 3. Ярость: тяжёлый доспех
# --------------------------------------------------------------------------
def test_rage_is_refused_in_heavy_armor():
    """Ярость в тяжёлом доспехе не включается — ни эффекта, ни траты.

    Было (до патча): `use_feature rage` в plate-armor давал `EffectApplied`
    и списывал применение. Стало: `CastFailed(reason='heavy_armor')`.
    Движок: `orchestrator._rage_armor_failure` — до траты действия.
    """
    handle = combat([member("barbarian", 5, ("plate-armor", "greataxe"))],
                    session="r177-armor")
    actor = next(c for c in O._get_live(handle).initiative if c.entity_id == "pc1")
    assert actor.worn_armor == "heavy", actor.worn_armor
    names, events = send(handle, intent_type="use_feature", feature_id="rage")
    assert reason(events, "CastFailed") == "heavy_armor", names
    state = visible(handle)
    assert not state["effects"].get("pc1"), state
    assert "feature_use:rage" not in state["counters"].get("pc1", {}), state

    # Контроль: тот же варвар без тяжёлого доспеха Ярость включает.
    light = combat([member("barbarian", 5, ("leather-armor", "greataxe"))],
                   session="r177-armor-light")
    names, events = send(light, intent_type="use_feature", feature_id="rage")
    assert "EffectApplied" in names, names
    assert visible(light)["counters"]["pc1"]["feature_use:rage"]["spent"] == 1


# --------------------------------------------------------------------------
# 4. Ярость: заклинание под ней отклоняется
# --------------------------------------------------------------------------
def test_raging_creature_cannot_cast_a_spell():
    """Под Яростью каст отклоняется с причиной, ячейка/действие не тратятся.

    Было (до патча): мультикласс варвар/волшебник в Ярости кастовал
    `fire-bolt` и наносил урон. Стало: `CastFailed(reason='raging')`.
    Движок: `orchestrator._raging_cast_failure`; SRD 5.2 Rage — «you can't
    cast spells».
    """
    handle = combat([member("barbarian", 5, ("greataxe",), "pc1", classes={"barbarian": 4, "wizard": 1},
                            scores=SMART)], session="r177-cast")
    names, events = send(handle, intent_type="use_feature", feature_id="rage")
    assert "EffectApplied" in names, names
    names, events = send(handle, intent_type="cast_spell", spell_id="fire-bolt",
                         target_id="m1")
    assert reason(events, "CastFailed") == "raging", names
    assert not rows(events, "DamageApplied"), names


# --------------------------------------------------------------------------
# 5. Непреклонная ярость
# --------------------------------------------------------------------------
def test_relentless_rage_holds_at_zero_hit_points():
    """Падение до 0 под Яростью: спасбросок Тел, хиты становятся 2×уровень.

    Было (до патча): хиты 0, событие `Death`, счётчика умения нет.
    Стало (seed 3): `SaveRolled(dc=10, ability='con')`, хиты 22 (варвар 11),
    счётчик `feature_use:relentless-rage` = 1, Ярость на месте, `Death` нет.
    Движок: `orchestrator._relentless_rage_hold`, вызов из
    `_emit_apply_damage` — единственного места, где урон становится хитами.
    """
    handle = combat([member("barbarian", 11, ("greataxe",), hp=7)],
                    foes=[foe(hp=100, ac=10, slug="ogre", name="Ogre")],
                    seed=3, session="r177-relentless")
    send(handle, intent_type="use_feature", feature_id="rage")
    for _ in range(8):
        send(handle, intent_type="attack", target_id="m1", weapon_id="greataxe")
        send(handle, intent_type="pass")
        _, events = monster_turn(handle)
        state = visible(handle)
        counter = state["counters"].get("pc1", {}).get("feature_use:relentless-rage")
        if counter:
            save = rows(events, "SaveRolled")
            assert save, events
            assert save[0]["ability"] == "con", save
            assert save[0]["dc"] == 10, save
            assert save[0]["succeeded"] is True, save
            assert state["hp"]["pc1"] == 22, state["hp"]
            assert counter["spent"] == 1, counter
            assert not rows(events, "Death"), events
            assert "effect:rage" in [e[0] for e in state["effects"].get("pc1", [])], state
            return
    raise AssertionError("ярость ни разу не удержала варвара на нуле: " + str(visible(handle)))


# --------------------------------------------------------------------------
# 6. Мастер на все руки в бою
# --------------------------------------------------------------------------
def test_jack_of_all_trades_reaches_the_combat_check_path():
    """Половина владения доходит до боевого пути проверок.

    Было (до патча): `check_modifier` возвращал proficiency=0 на навыке без
    владения. Стало: proficiency = Бонус владения // 2 (бард 5 → 1), итог =
    мод характеристики + 1; владение и экспертиза считаются как раньше.
    Движок: `activities/actor_stats.check_modifier` + `Combatant.jack_of_all_trades`
    (поле заполняет `build_party_member` из листа).
    """
    handle = combat([member("bard", 5, ("dagger",), choices=("skill:performance",))],
                    session="r177-jack")
    actor = next(c for c in O._get_live(handle).initiative if c.entity_id == "pc1")
    assert actor.jack_of_all_trades is True

    mod = O.check_modifier(actor, "int", "history")
    assert mod.proficiency == O.proficiency_bonus_of(actor) // 2, mod
    assert mod.total == mod.ability_mod + mod.proficiency, mod

    # Навык с владением правило не удваивает: там уже полный бонус
    # (выбор «skill:performance» задан в билде — иначе эта ветка молча не шла).
    assert "performance" in actor.skill_proficiencies, actor.skill_proficiencies
    proficient = O.check_modifier(actor, "cha", "performance")
    assert proficient.proficiency == O.proficiency_bonus_of(actor), proficient


# --------------------------------------------------------------------------
# 7. Изгнание нежити: минута и вынужденное бегство
# --------------------------------------------------------------------------
def test_turn_undead_lasts_a_minute_and_drives_the_target_away():
    """Эффект «Turned» держится 10 раундов и гонит цель от жреца.

    Было (до патча): длительность `seconds=60` не тикала (бой вставал на
    недееспособной цели), цель стояла на месте все раунды.
    Стало: `duration.seconds == 60`, тик 1 раунд за круг, через 10 кругов
    `EffectExpired(reason='duration')` и состояния сняты; на своём ходу
    нежить уходит прочь (`ActorMoved`, зона меняется).
    Движок: `orchestrator._is_turned` + `_apply_monster_flee_stance`; тик —
    `_expire_timed_effects_at_turn_end` (секунды → раунды, ключ — жрец).
    """
    handle = combat([member("cleric", 5, ("mace",))],
                    foes=[foe(hp=100, ac=12, zone="4,0", slug="skeleton",
                              creature_type="undead", name="Skeleton")],
                    width=12, height=9, seed=1, session="r177-turned")
    names, events = send(handle, intent_type="use_feature",
                         feature_id="channel-divinity-cleric",
                         activity_id=activity_id("channel-divinity-cleric", "Turn Undead"),
                         target_id="m1")
    assert "EffectApplied" in names, names
    state = visible(handle)
    turned = [(eid, dur) for eid, dur in state["effects"]["m1"] if eid == "effect:turned"]
    assert turned, state["effects"]
    assert turned[0][1]["seconds"] == 60, turned

    start_zone = state["zones"]["m1"]
    zones = []
    expired_with_duration = False
    for _ in range(26):
        live = O._get_live(handle)
        if live.current_actor_id == "m1":
            _, events = monster_turn(handle)
            zones.append(visible(handle)["zones"]["m1"])
        else:
            _, events = send(handle, intent_type="pass")
        state = visible(handle)
        if reason(events, "EffectExpired") == "duration":
            expired_with_duration = True
            break

    assert expired_with_duration, "эффект «Turned» не истёк за 10 раундов: " + str(zones)
    assert "m1" not in state["conditions"], state["conditions"]
    assert "effect:turned" not in [eid for eid, _ in state["effects"].get("m1", [])], state["effects"]
    assert zones and any(zone != start_zone for zone in zones), (
        f"цель не сдвинулась: {start_zone} -> {zones}")


#: Доказательства для `tests/test_class_coverage_proof.py` (вид `r177`).
R177_PROOFS = {
    "bardic_die_on_spell_attack": test_bardic_die_turns_a_missed_spell_attack,
    "bardic_die_on_save": test_bardic_die_turns_a_failed_saving_throw,
    "master_na_vse_ruki": test_jack_of_all_trades_reaches_the_combat_check_path,
    "nepreklonnaya_yarost": test_relentless_rage_holds_at_zero_hit_points,
    "yarost_heavy_armor": test_rage_is_refused_in_heavy_armor,
    "izgnanie_nezheti": test_turn_undead_lasts_a_minute_and_drives_the_target_away,
}


# --------------------------------------------------------------------------
# 5b. Непреклонная ярость: гейты «не с 0» и «не насмерть сразу»
# --------------------------------------------------------------------------
def test_relentless_rage_gates_on_falling_this_hit():
    """Умение не срабатывает на уроне, полученном уже на 0, и при Massive Damage.

    Найдено критиком r177: без гейтов урон лежащему на нуле лечил его обратно
    (каждый удар — новый бросок), а удар, убивающий сразу, обходился.
    Стало (SRD 5.2 «If you drop to 0 Hit Points … and don't die outright»):
    ``hp_before <= 0`` и остаток ``>=`` максимума хитов отсекаются до броска.
    Движок: ``orchestrator._relentless_rage_hold`` (аргументы ``hp_before``,
    ``damage``), вызов из ``_emit_apply_damage``.
    """
    handle = combat([member("barbarian", 11, ("greataxe",), hp=7)],
                    foes=[foe(hp=100, ac=10, slug="ogre", name="Ogre")],
                    seed=3, session="r177-relentless-gates")
    live = O._get_live(handle)
    assert O._relentless_rage_hold(live, "pc1", hp_before=0, damage=5) == 0
    assert O._relentless_rage_hold(live, "pc1", hp_before=5, damage=500) == 0


def test_massive_damage_kills_outright_even_when_raging():
    """Удар, убивающий сразу, Непреклонная ярость нейтрализовать не может.

    Максимум хитов 3, удар огра после сопротивления Ярости 6+ — остаток
    (урон − хиты до удара) больше максимума: `Death(reason='instant_kill')`,
    счётчика умения нет, хиты остаются нулём.
    """
    seen = []
    for seed in range(10):
        handle = combat([member("barbarian", 11, ("greataxe",), hp=3)],
                        foes=[foe(hp=100, ac=10, slug="ogre", name="Ogre")],
                        seed=seed, session="r177-massive")
        send(handle, intent_type="use_feature", feature_id="rage")
        send(handle, intent_type="attack", target_id="m1", weapon_id="greataxe")
        send(handle, intent_type="pass")
        _, events = monster_turn(handle)
        hit = [row for row in rows(events, "DamageApplied") if row["target_id"] == "pc1"]
        if not hit or hit[0]["amount"] < 6:
            continue
        seen.append((seed, hit[0]["amount"]))
        deaths = rows(events, "Death")
        assert deaths and deaths[0]["reason"] == "instant_kill", (seed, deaths)
        state = visible(handle)
        assert state["hp"]["pc1"] == 0, state["hp"]
        assert "feature_use:relentless-rage" not in state["counters"].get("pc1", {}), state
        return
    raise AssertionError("ни один удар огра на 10 зёрнах не дал остатка сверх максимума: " + str(seen))


# --------------------------------------------------------------------------
# 2b. Кость вдохновения и натуральная 1 на спасброске
# --------------------------------------------------------------------------
def test_bardic_die_is_drawn_on_a_natural_one_save():
    """Натуральная 1 не отменяет кость: авто-провал 1/20 есть только у атаки.

    Найдено критиком r177: страж `natural == 1` копировал правило атаки на
    спасбросок, хотя в корпусе спасбросок — `total_gte_dc` без натуральной 1
    (`rules_data/v2/chapters/phb-01/01-core.rules.json`), и провал, который
    кость могла перевернуть, её не получал.
    Движок: `activities/save_primitive._redeem_bardic_inspiration`.
    """
    import random
    from types import SimpleNamespace

    from dnd5e_engine.activities import save_primitive as P

    ctx = SimpleNamespace(granted_dices={"pc2": "1d8"}, granted_dice_rolls={},
                          rng=random.Random(4))
    target = SimpleNamespace(entity_id="pc2")
    total = P._redeem_bardic_inspiration(ctx, target, total=5, dc=14, natural=1)
    assert total > 5, total
    assert ctx.granted_dice_rolls["pc2"], ctx.granted_dice_rolls

    # Успешный спасбросок и авто-провал (natural=None) кость не берут.
    ctx2 = SimpleNamespace(granted_dices={"pc2": "1d8"}, granted_dice_rolls={},
                           rng=random.Random(4))
    assert P._redeem_bardic_inspiration(ctx2, target, total=15, dc=14, natural=8) == 15
    assert P._redeem_bardic_inspiration(ctx2, target, total=0, dc=14, natural=None) == 0
    assert not ctx2.granted_dice_rolls
