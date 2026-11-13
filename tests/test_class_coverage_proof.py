"""r131: доказанное покрытие классов — карта правил сверена с исполнением движка.

Каждое из 201 правил главы «Классы персонажей» получает статус, подтверждённый
прогоном: `engine_subset_verified` — движок исполняет (лист, число, активность,
отдельный механизм); `adjudication_required` — исполняет часть, дыра названа в
`blockers`; `blocked` — не исполняет, причина названа.

Сторож держит два источника в согласии: рабочую карту
(`rules_data/v2/chapters/phb-03-classes/*.json`) и таблицу доказательств этого
файла. Правило со статусом «исполняется» обязано иметь доказательство, которое
ДЕЙСТВИТЕЛЬНО проходит; правило без такого статуса не имеет права ссылаться на
исполнение.

Таблица снята прогоном 30.10.2026 на установленном движке этого дерева
(`.venv/.../dnd5e_engine`, зеркало `vendor/nat20`). Мутационная проверка сторожа
описана в `reports/ENGINE_WORKSPACE_PROGRESS.md` (раздел r131).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance, derive_sheet
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader
from solo.nat20_canonical import PhbSubclassLoader

ROOT = Path(__file__).resolve().parent.parent
CHAPTER = ROOT / "rules_data/v2/chapters/phb-03-classes"
SLUG_BY_FILE = {
    "01-bard.json": "bard", "02-barbarian.json": "barbarian", "03-fighter.json": "fighter",
    "04-wizard.json": "wizard", "05-druid.json": "druid", "06-cleric.json": "cleric",
    "07-warlock.json": "warlock", "08-monk.json": "monk", "09-paladin.json": "paladin",
    "10-rogue.json": "rogue", "11-ranger.json": "ranger", "12-sorcerer.json": "sorcerer",
}
CLASSES = sorted(SLUG_BY_FILE.values())
LEVELS = (1, 3, 5, 11, 20)
SCORES = AbilityScores(strength=13, dexterity=13, constitution=13,
                       intelligence=13, wisdom=13, charisma=13)
# r147: пакетный загрузчик + подклассы PHB 2024 (integration/nat20/phb-2024), чтобы
# доказательство «подкласс берётся» работало и для коллегий книги.
LOADER = PhbSubclassLoader()

STATUSES = {"adapter_verified", "kernel_verified", "engine_subset_verified",
            "adjudication_required", "blocked"}
IMPLEMENTATIONS = {"existing_engine_subset", "kernel_only_unexecuted",
                   "not_implemented", "kernel_verified"}

#: hp и число умений на уровнях 1/3/5/11/20 — вывод derive_sheet 30.10.2026.
EXPECTED_SHEET = {
    "barbarian": {1: (13, 6), 3: (29, 9), 5: (45, 11), 11: (93, 15), 20: (165, 20)},
    "bard": {1: (9, 5), 3: (21, 7), 5: (33, 8), 11: (69, 10), 20: (123, 12)},
    "cleric": {1: (9, 5), 3: (21, 6), 5: (33, 7), 11: (69, 9), 20: (123, 11)},
    "druid": {1: (9, 6), 3: (21, 8), 5: (33, 9), 11: (69, 10), 20: (123, 13)},
    "fighter": {1: (11, 6), 3: (25, 8), 5: (39, 10), 11: (81, 13), 20: (144, 15)},
    "monk": {1: (9, 5), 3: (21, 9), 5: (33, 12), 11: (69, 16), 20: (123, 21)},
    "paladin": {1: (11, 6), 3: (25, 9), 5: (39, 11), 11: (81, 15), 20: (144, 17)},
    "ranger": {1: (11, 6), 3: (25, 8), 5: (39, 9), 11: (81, 12), 20: (144, 17)},
    "rogue": {1: (9, 7), 3: (21, 9), 5: (33, 11), 11: (69, 14), 20: (123, 18)},
    "sorcerer": {1: (7, 5), 3: (17, 7), 5: (27, 8), 11: (57, 9), 20: (102, 10)},
    "warlock": {1: (9, 5), 3: (21, 6), 5: (33, 6), 11: (69, 8), 20: (123, 9)},
    "wizard": {1: (7, 6), 3: (17, 7), 5: (27, 8), 11: (57, 8), 20: (102, 10)},
}


# --------------------------------------------------------------------------
# Движок: лист и бой
# --------------------------------------------------------------------------
def build_spec(cls, level, equipment=(), choices=(), scores=SCORES, subclass=None):
    return CharacterBuildSpec(species_slug="human", background_slug="soldier",
                              class_slug=cls, classes={cls: level}, level=level,
                              ability_scores=scores, equipment=tuple(equipment),
                              selected_choices=tuple(choices), subclass_slug=subclass)


def sheet_of(cls, level, **kw):
    return derive_sheet(build_spec(cls, level, **kw), loader=LOADER).model_dump()


def party_member(cls, level, entity="pc1", zone="0,0", init=20, hp=200,
                 equipment=(), scores=SCORES, choices=()):
    return build_party_member(
        build_spec(cls, level, equipment=equipment, scores=scores, choices=choices),
        CombatInstance(entity_id=entity, name=entity.upper(), zone_id=zone,
                       initiative=init, hp_max=hp, hp_current=hp),
        loader=LOADER,
    )


def fight(party, seed=5, foe_zone="1,0"):
    handle = asyncio.run(O.start_combat(
        session_id="r131-proof", party=party,
        encounter=[EncounterMemberSpec(entity_id="m1", name="Foe", entity_type="Monster",
                                       initiative=1, hp_current=500, hp_max=500, ac=12,
                                       zone_id=foe_zone)],
        grid_scene=GridScene(width=9, height=7), rng_seed=seed,
    )).handle
    O.drain_pending_events(handle)
    return handle


def feature_use(cls, slug, level, **kw):
    """use_feature в живом бою: события и состояние после него."""
    party = kw.pop("party", None) or [party_member(cls, level,
                                                   equipment=kw.pop("equipment", ("longsword",)))]
    handle = fight(party, seed=kw.pop("seed", 5))
    intent = dict(intent_type="use_feature", feature_id=slug)
    intent.update(kw)
    asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(**intent)))
    return handle, O.drain_pending_events(handle)


# --------------------------------------------------------------------------
# Доказательства
# --------------------------------------------------------------------------
def _features(cls, level, **kw):
    rows = sheet_of(cls, level, **kw).get("features") or []
    return {row if isinstance(row, str) else (row.get("slug") or row.get("name")) for row in rows}


def _assert_feature(cls, level, slug):
    assert slug in _features(cls, level), f"derive_sheet не выдаёт {slug} на {cls} L{level}"


def _assert_class_attributes(cls, die):
    """Кость хитов, спасброски и владения оружием/доспехами — на листе."""
    sheet = sheet_of(cls, 1)
    assert sheet["hit_dice"] == {die: 1}, sheet["hit_dice"]
    assert len(sheet["save_proficiencies"]) == 2, sheet["save_proficiencies"]
    assert sheet["weapon_proficiencies"], "владения оружием пусты"
    assert sheet["armor_training"] is not None, "владения доспехами отсутствуют"


def _assert_spellcasting(cls, picks, ability):
    """Ячейки, заговоры, подготовленные заклинания и способность заклинаний."""
    sheet = sheet_of(cls, 5, choices=picks)
    slots = sheet["spell_slots"] or {}
    assert slots.get(1) == 4, slots
    assert sheet["prepared_spells"], "подготовленных заклинаний нет"
    if cls not in ("paladin", "ranger"):
        assert sheet["cantrips"], "заговоров нет"
    abilities = set((sheet["spell_abilities"] or {}).values())
    assert abilities == {ability}, abilities


def _assert_grows(cls, low, high, field, value):
    """Поле листа получает значение на старшем уровне и не имеет его на младшем."""
    before = set(sheet_of(cls, low)[field])
    after = set(sheet_of(cls, high)[field])
    assert value not in before, (cls, field, value, sorted(before))
    assert value in after, (cls, field, value, sorted(after))


def _assert_pact_rest():
    """Короткий отдых возвращает ячейки договора."""
    import random

    from dnd5e_engine.rest import HitDicePool, resolve_short_rest

    outcome = resolve_short_rest(HitDicePool(hit_die_size=8, dice_remaining=5, dice_total=5), 1, 2,
                                 rng=random.Random(7), pact_slots={1: 0}, pact_slot_max={1: 2})
    assert dict(outcome.pact_slots) == {1: 2}, outcome.pact_slots


def _assert_field(cls, level, field, expected, **kw):
    value = sheet_of(cls, level, **kw)[field]
    if isinstance(expected, (set, frozenset)):
        expected = sorted(expected)
    if isinstance(value, (set, frozenset)):
        value = sorted(value)
    assert value == expected, f"{cls} L{level}: {field} = {value!r}, ожидалось {expected!r}"


def _assert_truthy(cls, level, field, **kw):
    assert sheet_of(cls, level, **kw)[field], f"{cls} L{level}: {field} ложно"


def _assert_mastery(cls, level, picks):
    masteries = sheet_of(cls, level, equipment=tuple(picks),
                         choices=tuple(picks))["weapon_masteries"]
    assert sorted(masteries) == sorted(picks), (cls, picks, sorted(masteries))


def _assert_asi(cls, level):
    sheet = sheet_of(cls, level, choices=(f"asi:{cls}:{level}:strength+2",))
    assert sheet["ability_scores"]["strength"] == 15, "13 + 2 от ASI не пришли на лист"
    with pytest.raises(ValueError, match="Ability Score Improvement"):
        sheet_of(cls, level - 3 if level > 3 else 1,
                 choices=(f"asi:{cls}:1:strength+2",))


def _assert_expertise(cls, level, skill):
    sheet = sheet_of(cls, level, choices=(f"skill:{skill}", f"expertise:{skill}"))
    assert skill in set(sheet["skill_expertise"]), sheet["skill_expertise"]


def _assert_subclass(cls, subclass):
    """Подкласс берётся на 3 уровне, приносит свои умения и отвергается раньше."""
    base = _features(cls, 3)
    with_sub = _features(cls, 3, subclass=subclass)
    gained = with_sub - base
    assert gained, (cls, subclass, sorted(with_sub))
    with pytest.raises(ValueError, match="needs .* level 3"):
        sheet_of(cls, 1, subclass=subclass)


def _assert_style(cls, level):
    """Стиль применяется движком: Оборона +1 к КБ, Стрельба +2 к броску атаки."""
    with_style = derive_sheet(build_spec(cls, level, equipment=("chain-mail",),
                                         choices=("defense",)), loader=LOADER)
    without = derive_sheet(build_spec(cls, level, equipment=("chain-mail",)), loader=LOADER)
    assert with_style.ac == without.ac + 1, (with_style.ac, without.ac)

    def attack_bonus(picks):
        handle = fight([party_member(cls, level, equipment=("longbow",), choices=picks)],
                       seed=4, foe_zone="4,0")
        asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(
            intent_type="attack", target_id="m1", weapon_id="longbow")))
        rolls = [e.modifier for e in O.drain_pending_events(handle)
                 if type(e).__name__ == "AttackRolled"]
        assert rolls, "удар из лука не дал события броска"
        return rolls[0]

    assert attack_bonus(("archery",)) == attack_bonus(()) + 2


def _assert_feature_effect(cls, level, slug, expected, equipment="longsword",
                           target_id="m1"):
    """Умение в живом бою: активность выбирается из данных, ждём названное событие."""
    from dnd5e_engine.lib_loader import get_lib_loader

    feature = get_lib_loader().get_feature(slug)
    activities = list(feature.activities) if feature else []
    assert activities, f"у {slug} нет типизированных активностей"
    kw = {"target_id": target_id}
    if len(activities) > 1:
        kw["activity_id"] = activities[0].id
    _, events = feature_use(cls, slug, level, equipment=(equipment,), **kw)
    names = [type(e).__name__ for e in events]
    assert expected in names, (cls, slug, expected, names)


def _assert_extra(cls, level, extra):
    _assert_field(cls, level, "extra_attack_count", extra)
    handle = fight([party_member(cls, level, equipment=("longsword",))])
    assert O.get_live(handle).turn.attacks_remaining == extra + 1


def combat_second_wind():
    _, events = feature_use("fighter", "second-wind", 1, equipment=("longsword",))
    heal = [e.amount for e in events if type(e).__name__ == "HealingApplied"]
    assert heal and heal[0] > 0, [type(e).__name__ for e in events]


def combat_action_surge():
    handle = fight([party_member("fighter", 2, equipment=("longsword",))])
    assert O.get_live(handle).turn.extra_actions_remaining == 0
    asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(
        intent_type="use_feature", feature_id="action-surge")))
    O.drain_pending_events(handle)
    assert O.get_live(handle).turn.extra_actions_remaining == 1


def combat_rage():
    _, events = feature_use("barbarian", "rage", 5, equipment=("greataxe",))
    names = [type(e).__name__ for e in events]
    effects = [e.effect.model_dump() for e in events if type(e).__name__ == "EffectApplied"]
    assert names == ["IntentSubmitted", "EffectApplied"], names
    changes = {c["key"]: c["value"] for c in effects[0]["changes"]}
    assert changes["system.bonuses.mwak.damage"] == "+2", changes


def combat_rage_damage():
    """То же зерно с Яростью и без: разница — прибавка к урону Ярости."""
    def damage(raging, seed):
        member = party_member("barbarian", 5, equipment=("greataxe",))
        handle = fight([member], seed=seed)
        if raging:
            asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(
                intent_type="use_feature", feature_id="rage")))
            O.drain_pending_events(handle)
        asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(
            intent_type="attack", target_id="m1", weapon_id="greataxe")))
        return [e.amount for e in O.drain_pending_events(handle)
                if type(e).__name__ == "DamageApplied"]

    pairs = [(damage(False, seed), damage(True, seed)) for seed in range(6)]
    hits = [(plain[0], raged[0]) for plain, raged in pairs if plain and raged]
    assert hits, "ни одного попадания за 6 зёрен"
    assert all(raged - plain == 2 for plain, raged in hits), hits


def combat_bardic():
    party = [party_member("bard", 5, equipment=("dagger",)),
             party_member("fighter", 1, entity="pc2", zone="1,1", init=18)]
    handle, events = feature_use("bard", "bardic-inspiration", 5, party=party, target_id="pc2")
    assert "EffectApplied" in [type(e).__name__ for e in events], [type(e).__name__ for e in events]
    counters = O.get_live(handle).custom_counters_by_entity["pc1"]
    assert counters["feature_use:bardic-inspiration"]["spent"] == 1, counters


def combat_channel_divinity():
    _, events = feature_use("cleric", "channel-divinity-cleric", 2,
                            activity_id="UdbUwbvrWwgDuNy9", target_id="pc1")
    heal = [e.amount for e in events if type(e).__name__ == "HealingApplied"]
    assert heal and heal[0] > 0, [type(e).__name__ for e in events]


def combat_sear_undead():
    _, events = feature_use("cleric", "sear-undead", 5)
    names = [type(e).__name__ for e in events]
    assert "AreaTargeted" in names and "SaveRolled" in names, names


def combat_wild_shape():
    handle, events = feature_use("druid", "wild-shape", 5, form_id="wolf")
    assert "TempHpApplied" in [type(e).__name__ for e in events], [type(e).__name__ for e in events]
    form = O.get_live(handle).transformations["pc1"].form_slug
    assert form == "wolf", form


def combat_uncanny_metabolism():
    _, events = feature_use("monk", "uncanny-metabolism", 2)
    heal = [e.amount for e in events if type(e).__name__ == "HealingApplied"]
    assert heal and heal[0] > 0, [type(e).__name__ for e in events]


def combat_lay_on_hands():
    _, events = feature_use("paladin", "lay-on-hands", 1, target_id="pc1",
                            activity_id="gXZh9aGHcywV9huC", pool_points=5)
    heal = [e.amount for e in events if type(e).__name__ == "HealingApplied"]
    assert heal and heal[0] == 5, [type(e).__name__ for e in events]


def combat_favored_enemy():
    _, events = feature_use("ranger", "favored-enemy", 1, target_id="m1")
    names = [type(e).__name__ for e in events]
    assert "DamageApplied" in names and "EffectApplied" in names, names


def combat_sneak_attack():
    """Плут с союзником в 5 фт бьёт сильнее, чем без него: кости Коварной атаки."""
    def damage(with_ally, seed):
        rogue = party_member("rogue", 5, equipment=("rapier",),
                             scores=AbilityScores(strength=10, dexterity=17, constitution=14,
                                                  intelligence=10, wisdom=10, charisma=8))
        party = [rogue]
        if with_ally:
            party.append(party_member("fighter", 1, entity="pc2", zone="1,1", init=18,
                                      equipment=("longsword",)))
        handle = fight(party, seed=seed)
        asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(
            intent_type="attack", target_id="m1", weapon_id="rapier")))
        return [e.amount for e in O.drain_pending_events(handle)
                if type(e).__name__ == "DamageApplied"]

    pairs = [(damage(False, seed), damage(True, seed)) for seed in range(8)]
    hits = [(plain[0], sneak[0]) for plain, sneak in pairs if plain and sneak]
    assert len(hits) >= 4, pairs
    assert all(sneak > plain for plain, sneak in hits), hits


def combat_innate_sorcery():
    _, events = feature_use("sorcerer", "innate-sorcery", 1)
    names = [type(e).__name__ for e in events]
    assert names == ["IntentSubmitted", "EffectApplied"], names


def combat_cunning_dash():
    handle = fight([party_member("rogue", 2, equipment=("rapier",))])
    asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(
        intent_type="dash", use_bonus_action=True)))
    names = [type(e).__name__ for e in O.drain_pending_events(handle)]
    assert "DashTaken" in names, names


def combat_sorcerous_restoration():
    """Чародей: потраченное очко магии возвращается Чародейским восстановлением."""
    handle = fight([party_member("sorcerer", 6, equipment=("dagger",))])
    from dnd5e_engine.lib_loader import get_lib_loader

    acts = [a.id for a in get_lib_loader().get_feature("font-of-magic").activities]
    asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(
        intent_type="use_feature", feature_id="font-of-magic", activity_id=acts[0])))
    O.drain_pending_events(handle)
    spent = O.get_live(handle).custom_counters_by_entity["pc1"]["feature_use:font-of-magic"]["spent"]
    assert spent == 1, spent
    asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(
        intent_type="use_feature", feature_id="sorcerous-restoration")))
    O.drain_pending_events(handle)
    left = O.get_live(handle).custom_counters_by_entity["pc1"]["feature_use:font-of-magic"]["spent"]
    assert left == 0, left


def combat_heightened_focus():
    """Монах: Шквал ударов даёт 3 удара на 10 уровне вместо 2.

    Счётчик ударов Шквала лежит на боевом состоянии, но в `LiveCombatView` его
    нет — читаем приватное состояние движка только на чтение (проекции не
    хватает поля; записано в отчёт как ограничение).
    """
    from dnd5e_engine.lib_loader import get_lib_loader

    acts = [a.id for a in get_lib_loader().get_feature("monks-focus").activities]
    flurry = {}
    for level in (9, 10):
        handle = fight([party_member("monk", level, equipment=())])
        asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(
            intent_type="use_feature", feature_id="monks-focus", activity_id=acts[0])))
        O.drain_pending_events(handle)
        live = O._get_live(handle)
        actor = live.initiative[live.current_turn_index]
        flurry[level] = actor.flurry_strikes_remaining
    assert flurry == {9: 2, 10: 3}, flurry


def combat_magical_cunning():
    """Колдун: интент принимается и тратит использование.

    Восстановление ячеек договора при этом НЕ доказано (пул был полон, а
    заклинания колдуна без выбранных ходов не кастуются) — поэтому в карте
    правило стоит `adjudication_required`, а не зелёным.
    """
    handle, _ = feature_use("warlock", "magical-cunning", 5)
    counters = O.get_live(handle).custom_counters_by_entity["pc1"]
    assert counters["feature_use:magical-cunning"]["spent"] == 1, counters


def combat_arcane_recovery():
    """Волшебник: интент принимается и тратит использование; восстановление не доказано."""
    handle, _ = feature_use("wizard", "arcane-recovery", 5)
    counters = O.get_live(handle).custom_counters_by_entity["pc1"]
    assert counters["feature_use:arcane-recovery"]["spent"] == 1, counters


COMBAT = {
    "second_wind": combat_second_wind,
    "action_surge": combat_action_surge,
    "rage": combat_rage,
    "rage_damage": combat_rage_damage,
    "bardic": combat_bardic,
    "channel_divinity": combat_channel_divinity,
    "sear_undead": combat_sear_undead,
    "wild_shape": combat_wild_shape,
    "uncanny": combat_uncanny_metabolism,
    "lay_on_hands": combat_lay_on_hands,
    "favored_enemy": combat_favored_enemy,
    "sneak_attack": combat_sneak_attack,
    "innate_sorcery": combat_innate_sorcery,
    "cunning_dash": combat_cunning_dash,
    "sorcerous_restoration": combat_sorcerous_restoration,
    "heightened_focus": combat_heightened_focus,
    "magical_cunning": combat_magical_cunning,
    "arcane_recovery": combat_arcane_recovery,
}

PROOF_LABEL = {
    "second_wind": "Второе дыхание: лечение в бою",
    "action_surge": "Всплеск действий: дополнительное действие",
    "rage": "Ярость: эффект с прибавкой к урону",
    "rage_damage": "Ярость: +2 к урону тем же зерном",
    "bardic": "Бардовское вдохновение: кость выдана",
    "channel_divinity": "Проведение божественности: лечение",
    "sear_undead": "Испепеление нежити: область и спасбросок",
    "wild_shape": "Дикий облик: форма волка и временные хиты",
    "uncanny": "Феноменальный метаболизм: лечение",
    "lay_on_hands": "Возложение рук: 5 хитов из пула",
    "favored_enemy": "Избранный враг: урон и эффекты",
    "sneak_attack": "Коварная атака: кости в уроне",
    "innate_sorcery": "Врождённое чародейство: эффект",
    "cunning_dash": "Хитрое действие: Рывок бонусным действием",
    "sorcerous_restoration": "Чародейское восстановление: очко магии 1 → 0",
    "heightened_focus": "Повышенная сосредоточенность: удары Шквала 2 → 3",
    "magical_cunning": "Магическая изворотливость: принято, восстановление не доказано",
    "arcane_recovery": "Магическое восстановление: принято, восстановление не доказано",
}


def run_proof(proof):
    kind, *args = proof
    if kind == "feature":
        _assert_feature(*args)
    elif kind == "field":
        _assert_field(*args)
    elif kind == "truthy":
        _assert_truthy(*args)
    elif kind == "mastery":
        _assert_mastery(*args)
    elif kind == "asi":
        _assert_asi(*args)
    elif kind == "expertise":
        _assert_expertise(*args)
    elif kind == "subclass":
        _assert_subclass(*args)
    elif kind == "style":
        _assert_style(*args)
    elif kind == "extra":
        _assert_extra(*args)
    elif kind == "attributes":
        _assert_class_attributes(*args)
    elif kind == "spellcasting":
        _assert_spellcasting(*args)
    elif kind == "grows":
        _assert_grows(*args)
    elif kind == "pact_rest":
        _assert_pact_rest()
    elif kind == "effect":
        _assert_feature_effect(*args)
    elif kind == "combat":
        COMBAT[args[0]]()
    elif kind == "r175":
        # r175: доказательство хост-пути и эффекта — tests/test_class_features_reach_game.py
        from test_class_features_reach_game import R175_PROOFS

        R175_PROOFS[args[0]]()
    elif kind == "r177":
        # r177: доказательство закрытых дыр вендора — tests/test_vendor_five_feature_gaps.py
        from test_vendor_five_feature_gaps import R177_PROOFS

        R177_PROOFS[args[0]]()
    elif kind == "r146":
        # r146: доказательство эффекта лежит в tests/test_effect_proof_classes.py;
        # импорт по месту, чтобы карта r131 не зависела от порядка сборки.
        from test_effect_proof_classes import R146_PROOFS

        R146_PROOFS[".".join(args[0]) if isinstance(args[0], tuple) else args[0]]()
    else:  # pragma: no cover - защита от опечатки в таблице
        raise AssertionError(f"неизвестный вид доказательства: {kind}")


# --------------------------------------------------------------------------
# Каталог: статус каждого правила и чем он подтверждён
# --------------------------------------------------------------------------
def V(proof, handler=None):
    return dict(status="engine_subset_verified", handler=handler,
                implementation="existing_engine_subset", blockers=(), proof=proof)


def A(blockers, handler=None):
    return dict(status="adjudication_required", handler=handler,
                implementation="existing_engine_subset", blockers=tuple(blockers), proof=None)


def B(blockers):
    return dict(status="blocked", handler=None, implementation="not_implemented",
                blockers=tuple(blockers), proof=None)


BARD, BARB, FIGHTER, WIZARD = "bard", "barbarian", "fighter", "wizard"
DRUID, CLERIC, WARLOCK, MONK = "druid", "cleric", "warlock", "monk"
PALADIN, ROGUE, RANGER, SORCERER = "paladin", "rogue", "ranger", "sorcerer"

CATALOG = {
    # ---------------- Бард ----------------
    (BARD, "class_attributes"): V(("attributes", BARD, 8)),
    (BARD, "vardo_vdohnovenie"): A([
        # r175: путь хоста готов и проверен на проекции двух героев, но в бою
        # кампании герой один — давать кость некому. Это дыра хоста, а не движка.
        "в бою кампании хоста один герой — получателя кости нет; путь хоста готов и проверен на проекции двух героев",
    ], handler="nat20_content.feature_gate"),
    (BARD, "sotvorenie_zaklinanii"): V(("spellcasting", BARD, ("cantrip:vicious-mockery@bard", "cantrip:minor-illusion@bard", "spell:healing-word@bard"), "cha")),
    (BARD, "ekspertnost"): V(("expertise", BARD, 2, "stealth")),
    # r177: боевой путь проверок читает половину владения (было — только
    # владение и экспертиза), доказано прогоном `check_modifier` в живом бою.
    (BARD, "master_na_vse_ruki"): V(("r177", "master_na_vse_ruki")),
    (BARD, "podklass_barda"): V(("subclass", BARD, "lore")),
    (BARD, "uvelichenie_harakteristik"): V(("asi", BARD, 4)),
    (BARD, "istochnik_vdohnoveniya"): A([
        "движок принимает умение и тратит использование, но восстановление костей вдохновения прогоном не наблюдается",
    ]),
    (BARD, "kontrocharovanie"): A([
        "активность реакции принимается, но преимущество на спасбросок от очарования и испуга прогоном не подтверждено",
    ]),
    (BARD, "tainy_magii"): B([
        "выбранные через Тайны магии заклинания не доходят до живого гейта умений — движок выдаёт имя умения, а выбор не исполняется",
    ]),
    (BARD, "prevoshodnoe_vdohnovenie"): B([
        "у умения нет ни типизированной активности, ни поля листа: восстановление костей на броске инициативы исполнять нечем",
    ]),
    (BARD, "epicheskii_dar"): A([
        "дар не входит в репертуар ни одного класса: use_feature даёт CastFailed(not_in_repertoire)",
        "выгод дара нет: activities и passive_effects пусты в данных, слово epic в ядре встречается только в labels.py",
    ]),
    (BARD, "slova_tvoreniya"): B([
        "у умения нет типизированной активности: усиление слов силы движком не исполняется",
    ]),
    (BARD, "kolegiya_dobletsi_boevoe_vdohnovenie"): A([
        "умение «Боевое вдохновение» перенесено дословно (PHB 2024, стр. 56), но типизированной активности в движке нет: трату Кости вдохновения и добавку к КЗ или урону исполняет Мастер; в библиотеке пина записи умения нет — use_feature отвечает CastFailed(not_in_repertoire), и это видно в прогоне r170",
    ]),
    (BARD, "kolegiya_znanii_ostroe_slovtso"): B([
        "активность «Острого словца» принимается, но ни эффекта, ни траты ресурса не даёт",
    ]),
    (BARD, "kolegiya_ocharovaniya_maniya_vdohnoveniya"): A([
        "умение «Мантия вдохновения» перенесено дословно (PHB 2024, стр. 58), но типизированной активности в движке нет: временные хиты и Реакцию-перемещение исполняет Мастер; в библиотеке пина записи умения нет — use_feature отвечает CastFailed(not_in_repertoire), и это видно в прогоне r170",
    ]),
    (BARD, "kolegiya_tantsa_oslepitelnye_fuete"): A([
        "умение «Ослепительные фуэте» перенесено дословно (PHB 2024, стр. 59), но типизированной активности в движке нет: КЗ без доспехов, Безоружный удар и Бардовский урон исполняет Мастер; в библиотеке пина записи умения нет — use_feature отвечает CastFailed(not_in_repertoire), и это видно в прогоне r170",
    ]),

    # ---------------- Варвар ----------------
    (BARB, "class_attributes"): V(("attributes", BARB, 12)),
    (BARB, "yarost"): V(("r175", "yarost"), handler="nat20_content.feature_gate"),
    (BARB, "zashita_bez_dospehov"): V(("field", BARB, 1, "ac", 12)),
    (BARB, "iskusnoe_vladenie_oruzhiem"): V(("mastery", BARB, 1, ("longsword", "greataxe"))),
    (BARB, "chuvstvo_opasnosti"): B([
        "у умения нет активности, а его пассивный эффект не привязан ни к одному исполняемому пути — преимущество на спасброски Ловкости не выдаётся",
    ]),
    (BARB, "bezrassudnaya_ataka"): B([
        "умение не исполняется: движок отвечает `CastFailed` с причиной `class_feature_no_typed_activities`",
    ]),
    (BARB, "podklass_varvara"): V(("subclass", BARB, "berserker")),
    (BARB, "pervobytnoe_znanie"): B([
        "выбор навыка по Первобытному знанию не доходит до листа: у умения нет ни активности, ни поля",
    ]),
    (BARB, "uvelichenie_harakteristik"): V(("asi", BARB, 4)),
    (BARB, "dopolnitelnaya_ataka"): V(("extra", BARB, 5, 1)),
    (BARB, "bystroe_peredvizhenie"): V(("field", BARB, 5, "base_speed", 40)),
    (BARB, "dikii_instinkt"): V(("r146", ("barbarian", "dikii_instinkt"))),
    (BARB, "instinktivnyi_brosok"): B([
        "у умения нет типизированной активности: рывок при броске инициативы не исполняется",
    ]),
    (BARB, "zhestokii_udar"): V(("combat", "rage")),
    # r177: падение до 0 под Яростью даёт спасбросок Тел Сл 10 (+5 за
    # применение) и меняет хиты на 2 × уровень Варвара — доказано живым боем.
    (BARB, "nepreklonnaya_yarost"): V(("r177", "nepreklonnaya_yarost")),
    (BARB, "uluchshennyi_zhestokii_udar_13"): V(("effect", BARB, 13, "improved-brutal-strike",
                                                "EffectApplied", "greataxe")),
    (BARB, "nepreryvnaya_yarost"): V(("r175", "nepreryvnaya_yarost")),
    (BARB, "uluchshennyi_zhestokii_udar_17"): B([
        "у умения 17 уровня нет типизированной активности в корпусе: усиление Жестокого удара не исполняется",
    ]),
    (BARB, "neukrotimaya_mosh"): B([
        "у умения нет ни активности, ни поля листа: замена броска Силы на 20 не исполняется",
    ]),
    (BARB, "epicheskii_dar"): A([
        "дар не входит в репертуар ни одного класса: use_feature даёт CastFailed(not_in_repertoire)",
        "выгод дара нет: activities и passive_effects пусты в данных, слово epic в ядре встречается только в labels.py",
    ]),
    (BARB, "dikii_chempion"): A([
        "увеличение характеристик на 20 уровне до 25 движком не применяется: растут только ASI-черты с пределом 20",
    ]),

    # ---------------- Воин ----------------
    (FIGHTER, "class_attributes"): V(("attributes", FIGHTER, 10)),
    (FIGHTER, "boevoi_stil"): V(("style", FIGHTER, 1), handler="CombatEngine.attack"),
    (FIGHTER, "vtoroe_dyhanie"): V(("combat", "second_wind"), handler="CombatEngine.bonus"),
    (FIGHTER, "iskusnoe_vladenie_oruzhiem"): V(("mastery", FIGHTER, 1, ("longsword", "greatsword"))),
    (FIGHTER, "vsplesk_deistvii"): V(("combat", "action_surge")),
    (FIGHTER, "takticheskoe_myshlenie"): A([
        "движок принимает умение и тратит использование, но добавка к проверке прогоном не наблюдается",
    ]),
    (FIGHTER, "podklass_voina"): V(("subclass", FIGHTER, "champion")),
    (FIGHTER, "uvelichenie_harakteristik"): V(("asi", FIGHTER, 4)),
    (FIGHTER, "dopolnitelnaya_ataka"): V(("extra", FIGHTER, 5, 1)),
    (FIGHTER, "takticheskoe_peremeshenie"): B([
        "у умения нет типизированной активности: перемещение половиной скорости не исполняется",
    ]),
    (FIGHTER, "upornyi"): A([
        "движок принимает умение и тратит использование, но переброс спасброска прогоном не наблюдается",
    ]),
    (FIGHTER, "master_taktiki"): B([
        "у умения нет активности: замена мастерства оружия не исполняется",
    ]),
    (FIGHTER, "dve_dopolnitelnye_ataki"): V(("extra", FIGHTER, 11, 2)),
    (FIGHTER, "vyverennye_ataki"): B([
        "у умения нет активности: преимущество после промаха не исполняется",
    ]),
    (FIGHTER, "tri_dopolnitelnye_ataki"): V(("extra", FIGHTER, 20, 3)),
    (FIGHTER, "epicheskii_dar"): A([
        "дар не входит в репертуар ни одного класса: use_feature даёт CastFailed(not_in_repertoire)",
        "выгод дара нет: activities и passive_effects пусты в данных, слово epic в ядре встречается только в labels.py",
    ]),

    # ---------------- Волшебник ----------------
    (WIZARD, "class_attributes"): V(("attributes", WIZARD, 6)),
    (WIZARD, "sotvorenie_zaklinanii"): V(("spellcasting", WIZARD, ("cantrip:fire-bolt@wizard", "spell:magic-missile@wizard"), "int")),
    (WIZARD, "znatok_ritualov"): A([
        "ритуал в бою отвергается движком (`ritual_in_combat`), а внебоевой путь `resolve_ritual_cast` прогоном не подтверждён",
    ]),
    (WIZARD, "magicheskoe_vosstanovlenie"): A([
        "движок принимает умение и тратит использование, но восстановление ячеек на коротком отдыхе прогоном не наблюдается",
    ]),
    (WIZARD, "akademicheskie_znaniya"): B([
        "у умения нет ни активности, ни поля листа: выбор навыка по Академическим знаниям не исполняется",
    ]),
    (WIZARD, "podklass_volshebnika"): V(("subclass", WIZARD, "evoker")),
    (WIZARD, "uvelichenie_harakteristik"): V(("asi", WIZARD, 4)),
    (WIZARD, "zapominanie_zaklinaniya"): B([
        "у умения нет типизированной активности: замена подготовленного заклинания не исполняется",
    ]),
    (WIZARD, "masterstvo_zaklinatelya"): A([
        "движок принимает умение и тратит использование, но выбранное заклинание 1 и 2 уровня без ячейки не кастуется",
    ]),
    (WIZARD, "epicheskii_dar"): A([
        "дар не входит в репертуар ни одного класса: use_feature даёт CastFailed(not_in_repertoire)",
        "выгод дара нет: activities и passive_effects пусты в данных, слово epic в ядре встречается только в labels.py",
    ]),
    (WIZARD, "firmennye_zaklinaniya"): A([
        "движок принимает умение и тратит использование, но бесплатное применение фирменных заклинаний не наблюдается",
    ]),
    (WIZARD, "kopirovanie_zaklinanii"): B([
        "копирование в книгу — внебоевое действие без активности и без пути в движке",
    ]),

    # ---------------- Друид ----------------
    (DRUID, "class_attributes"): V(("attributes", DRUID, 8)),
    (DRUID, "sotvorenie_zaklinanii"): V(("spellcasting", DRUID, ("cantrip:guidance@druid", "spell:cure-wounds@druid"), "wis")),
    (DRUID, "druidicheskii_yazyk"): B([
        "у умения нет ни активности, ни поля листа: язык и общение со зверями не исполняются",
    ]),
    (DRUID, "pervozdannyi_orden"): B([
        "выбор ордена не доходит до листа: у умения нет ни активности, ни поля",
    ]),
    (DRUID, "dikii_oblik"): V(("r175", "dikii_oblik"), handler="nat20_content.feature_gate"),
    (DRUID, "dikii_sputnik"): A([
        "движок принимает умение и тратит использование Дикого облика, но призыв спутника не разрешается: активность призыва вне белого списка",
    ]),
    (DRUID, "podklass_druida"): V(("subclass", DRUID, "land")),
    (DRUID, "uvelichenie_harakteristik"): V(("asi", DRUID, 4)),
    (DRUID, "dikoe_vosstanovlenie"): A([
        "движок принимает умение и тратит использование, но обмен ячейки на форму не наблюдается",
    ]),
    (DRUID, "yarost_stihii"): B([
        "у умения нет типизированной активности: прибавка урона стихией не исполняется",
    ]),
    (DRUID, "uluchshennaya_yarost_stihii"): B([
        "у умения нет активности: дальность и урон Ярости стихий не исполняются",
    ]),
    (DRUID, "zaklinaniya_zverya"): B([
        "заклинания в форме зверя не смоделированы (матрица апстрима: Beast Spells not modelled)",
    ]),
    (DRUID, "arhidruid"): A([
        "движок принимает умение и тратит использование, но бесконечные применения Дикого облика не наблюдаются",
    ]),
    (DRUID, "epicheskii_dar"): A([
        "дар не входит в репертуар ни одного класса: use_feature даёт CastFailed(not_in_repertoire)",
        "выгод дара нет: activities и passive_effects пусты в данных, слово epic в ядре встречается только в labels.py",
    ]),

    # ---------------- Жрец ----------------
    (CLERIC, "class_attributes"): V(("attributes", CLERIC, 8)),
    (CLERIC, "sotvorenie_zaklinanii"): V(("spellcasting", CLERIC, ("cantrip:guidance@cleric", "spell:cure-wounds@cleric"), "wis")),
    (CLERIC, "bozhestvennyi_orden"): B([
        "выбор ордена не доходит до листа: у умения нет ни активности, ни поля",
    ]),
    (CLERIC, "provedenie_bozhestvennosti"): V(("combat", "channel_divinity")),
    (CLERIC, "bozhestvennaya_iskra"): V(("combat", "channel_divinity")),
    (CLERIC, "izgnanie_nezheti"): V(("r175", "izgnanie_nezheti"),
                                    handler="nat20_content.feature_gate"),
    (CLERIC, "podklass_zheretsa"): V(("subclass", CLERIC, "life")),
    (CLERIC, "uvelichenie_harakteristik"): V(("asi", CLERIC, 4)),
    (CLERIC, "ispeplenie_nezheti"): V(("combat", "sear_undead")),
    (CLERIC, "blagoslovlennye_udary"): B([
        "у умения нет типизированной активности: выбор урона Благословлённых ударов не исполняется",
    ]),
    (CLERIC, "bozhestvennoe_vmeshatelstvo"): A([
        "движок принимает умение и тратит использование, но исполнение заклинания вмешательством не наблюдается",
    ]),
    (CLERIC, "uluchshennye_blagoslovlennye_udary"): V(("effect", CLERIC, 14,
                                                      "improved-blessed-strikes", "TempHpApplied")),
    (CLERIC, "velikoe_bozhestvennoe_vmeshatelstvo"): A([
        "движок принимает умение и тратит использование, но выбор заклинания вмешательством не наблюдается",
    ]),
    (CLERIC, "epicheskii_dar"): A([
        "дар не входит в репертуар ни одного класса: use_feature даёт CastFailed(not_in_repertoire)",
        "выгод дара нет: activities и passive_effects пусты в данных, слово epic в ядре встречается только в labels.py",
    ]),

    # ---------------- Колдун ----------------
    (WARLOCK, "class_attributes"): V(("attributes", WARLOCK, 8)),
    (WARLOCK, "tainstvennye_vozzvaniya"): B([
        "выбранные воззвания не доходят до живого гейта умений: движок выдаёт имя умения, а выбор не исполняется",
    ]),
    (WARLOCK, "magiya_dogovora"): V(("pact_rest",)),
    (WARLOCK, "magicheskaya_izvorotlivost"): A([
        "движок принимает умение и тратит использование, но восстановление ячеек договора прогоном не наблюдается",
    ]),
    (WARLOCK, "podklass_kolduna"): V(("subclass", WARLOCK, "fiend")),
    (WARLOCK, "uvelichenie_harakteristik"): V(("asi", WARLOCK, 4)),
    (WARLOCK, "svyaz_s_pokrovitelem"): B([
        "у умения нет типизированной активности: связь с покровителем не исполняется",
    ]),
    (WARLOCK, "tainstvennyi_arkanum"): B([
        "у умения нет типизированной активности: тайный арканум не исполняется",
    ]),
    (WARLOCK, "vedmachii_vzor"): B([
        "воззвания не доходят до живого гейта умений: у листа нет ни умения, ни поля для ведьмачьего взора",
    ]),
    (WARLOCK, "vzor_dvuh_umov"): B([
        "воззвания не доходят до живого гейта умений: взор двух умов не исполняется",
    ]),
    (WARLOCK, "dar_glubin"): B([
        "воззвания не доходят до живого гейта умений: дар глубин не исполняется",
    ]),
    (WARLOCK, "tainstvennyi_master"): A([
        "движок принимает умение и тратит использование, но восстановление всех ячеек договора не наблюдается",
    ]),
    (WARLOCK, "epicheskii_dar"): A([
        "дар не входит в репертуар ни одного класса: use_feature даёт CastFailed(not_in_repertoire)",
        "выгод дара нет: activities и passive_effects пусты в данных, слово epic в ядре встречается только в labels.py",
    ]),

    # ---------------- Монах ----------------
    (MONK, "class_attributes"): V(("attributes", MONK, 8)),
    (MONK, "boevye_iskusstva"): V(("r175", "boevye_iskusstva"),
                                  handler="nat20_content.feature_gate"),
    (MONK, "zashita_bez_dospehov"): V(("field", MONK, 1, "ac", 12)),
    (MONK, "monasheskaya_sosredotochennost"): V(("r175", "monasheskaya_sosredotochennost"),
                                               handler="nat20_content.feature_gate"),
    (MONK, "shkval_udarov"): V(("r175", "shkval_udarov")),
    (MONK, "terpelivaya_oborona"): A([
        "эффект применяется, но состояние «уклонение» от Терпеливой обороны движком не выдаётся",
    ]),
    (MONK, "postup_vetra"): A([
        "эффект применяется, но Рывок по Поступи ветра не исполняется",
    ]),
    (MONK, "dvizhenie_bez_dospehov"): B([
        "прибавка к скорости не попадает на лист: базовая скорость 30 и на 2, и на 9 уровне",
    ]),
    (MONK, "fenomenalnyi_metabolizm"): V(("combat", "uncanny")),
    (MONK, "otrazhenie_atak"): A([
        "снижение урона исполняется, но перенаправление атаки за Очко фокуса не подтверждено",
    ]),
    (MONK, "podklass_monaha"): V(("subclass", MONK, "hand")),
    (MONK, "uvelichenie_harakteristik"): V(("asi", MONK, 4)),
    (MONK, "medlennoe_padenie"): V(("effect", MONK, 4, "slow-fall", "HealingApplied")),
    (MONK, "dopolnitelnaya_ataka"): V(("extra", MONK, 5, 1)),
    (MONK, "oshelomlyayushii_udar"): A([
        "умение срабатывает отдельным Действием, а не на попадании: повтор во втором интенте того же хода отвергнут no_action_economy",
        "ограничение «раз в ход» подменено бюджетом Действий",
    ]),
    (MONK, "usilennye_udary"): V(("effect", MONK, 6, "empowered-strikes", "DamageApplied")),
    (MONK, "uvertlivost"): B([
        "у умения нет ни активности, ни поля листа: уклонение от урона области не исполняется",
    ]),
    (MONK, "akrobatichnoe_peremeshenie"): B([
        "у умения нет активности: ходьба по стенам и воде не исполняется",
    ]),
    (MONK, "povyshennaya_sosredotochennost"): V(("combat", "heightened_focus")),
    (MONK, "samovosstanovlenie"): B([
        "умения нет в корпусе этого движка: лист его не выдаёт ни на одном уровне",
    ]),
    (MONK, "otrazhenie_energii"): V(("effect", MONK, 13, "deflect-energy", "HealingApplied")),
    (MONK, "obuchennyi_vyzhivat"): A([
        "движок принимает умение, но снятие состояний на отдыхе не наблюдается",
    ]),
    (MONK, "polnaya_sosredotochennost"): B([
        "у умения нет типизированной активности: восстановление Очков фокуса не исполняется",
    ]),
    (MONK, "sovershennaya_zashita"): V(("effect", MONK, 18, "superior-defense", "EffectApplied")),
    (MONK, "telo_i_razum"): A([
        "увеличение Ловкости и Мудрости до 25 движком не применяется: растут только ASI-черты с пределом 20",
    ]),
    (MONK, "epicheskii_dar"): A([
        "дар не входит в репертуар ни одного класса: use_feature даёт CastFailed(not_in_repertoire)",
        "выгод дара нет: activities и passive_effects пусты в данных, слово epic в ядре встречается только в labels.py",
    ]),

    # ---------------- Паладин ----------------
    (PALADIN, "class_attributes"): V(("attributes", PALADIN, 10)),
    (PALADIN, "vozlozhenie_ruk"): V(("combat", "lay_on_hands")),
    (PALADIN, "sotvorenie_zaklinanii"): V(("spellcasting", PALADIN, ("spell:cure-wounds@paladin",), "cha")),
    (PALADIN, "iskusnoe_vladenie_oruzhiem"): V(("mastery", PALADIN, 1,
                                                ("longsword", "greatsword"))),
    (PALADIN, "boevoi_stil"): V(("style", PALADIN, 2), handler="CombatEngine.attack"),
    (PALADIN, "kara_paladina"): B([
        "Кара паладина остаётся прозой: у умения нет типизированной активности (матрица апстрима: Divine Smite inert)",
    ]),
    (PALADIN, "provedenie_bozhestvennosti"): A([
        "единственная активность Канала — «Божественное чувство» с пустым эффектом (changes=[], statuses=[])",
        "восстановление одного применения на коротком отдыхе не проверено",
    ]),
    (PALADIN, "bozhestvennoe_chuvstvo"): B([
        "у умения нет ни активности, ни поля листа: Божественное чувство не исполняется",
    ]),
    (PALADIN, "podklass_paladina"): V(("subclass", PALADIN, "devotion")),
    (PALADIN, "uvelichenie_harakteristik"): V(("asi", PALADIN, 4)),
    (PALADIN, "dopolnitelnaya_ataka"): V(("extra", PALADIN, 5, 1)),
    (PALADIN, "vernyi_skakun"): B([
        "у умения нет типизированной активности: призыв скакуна не исполняется",
    ]),
    (PALADIN, "aura_zashity"): A([
        "движок принимает умение, но добавка Харизмы к спасброскам союзников прогоном не наблюдается",
    ]),
    (PALADIN, "poritsanie_vragov"): A([
        "применение не списывает Канал божественности: счётчик feature_use:channel-divinity-paladin не появляется",
        "Сл по Харизме, дистанция 60 футов и число целей (мод. Харизмы) прогоном не сверяются",
    ]),
    (PALADIN, "aura_otvagi"): B([
        "аура не выдаёт ни эффекта, ни иммунитета: лист 10 уровня без immunities, use_feature — только IntentSubmitted",
    ]),
    (PALADIN, "siyayushie_udary"): B([
        "у умения нет типизированной активности: прибавка урона Светом не исполняется",
    ]),
    (PALADIN, "vosstanavlivayushee_prikosnovenie"): A([
        "движок принимает умение, но снятие состояния прикосновением не наблюдается",
    ]),
    (PALADIN, "rasshirenie_aury"): B([
        "у умения нет активности: расширение аур до 30 футов не исполняется",
    ]),
    (PALADIN, "epicheskii_dar"): A([
        "дар не входит в репертуар ни одного класса: use_feature даёт CastFailed(not_in_repertoire)",
        "выгод дара нет: activities и passive_effects пусты в данных, слово epic в ядре встречается только в labels.py",
    ]),

    # ---------------- Плут ----------------
    (ROGUE, "class_attributes"): V(("attributes", ROGUE, 8)),
    (ROGUE, "ekspertnost"): V(("expertise", ROGUE, 1, "stealth")),
    (ROGUE, "kovarnaya_ataka"): V(("combat", "sneak_attack"), handler="CombatEngine.damage"),
    (ROGUE, "vorovskoi_zhargon"): B([
        "у умения нет ни активности, ни поля листа: жаргон не исполняется",
    ]),
    (ROGUE, "iskusnoe_vladenie_oruzhiem"): V(("mastery", ROGUE, 1, ("rapier", "dagger"))),
    (ROGUE, "hitroe_deistvie"): V(("combat", "cunning_dash")),
    (ROGUE, "podklass_pluta"): V(("subclass", ROGUE, "thief")),
    (ROGUE, "tochnoe_pritselivanie"): B([
        "преимущество не выдаётся: следующая атака после Точного прицеливания шла с обычным броском",
    ]),
    (ROGUE, "uvelichenie_harakteristik"): V(("asi", ROGUE, 4)),
    (ROGUE, "hitryi_udar"): V(("effect", ROGUE, 5, "cunning-strike", "SaveRolled", "rapier")),
    (ROGUE, "neveroyatnoe_uklonenie"): A([
        "движок принимает умение, но уменьшение урона вдвое прогоном не наблюдается",
    ]),
    (ROGUE, "uvertlivost"): B([
        "у умения нет ни активности, ни поля листа: уклонение от урона области не исполняется",
    ]),
    (ROGUE, "nadezhnyi_talant"): A([
        "лист выставляет признак надёжного таланта, но боевой путь проверок его не берёт",
    ]),
    (ROGUE, "uluchshennyi_hitryi_udar"): B([
        "у умения нет активности: новые варианты Хитрого удара не исполняются",
    ]),
    (ROGUE, "beschestnye_udary"): V(("effect", ROGUE, 14, "devious-strikes", "SaveRolled", "rapier")),
    (ROGUE, "skolzskii_um"): V(("grows", ROGUE, 14, 15, "save_proficiencies", "wis")),
    (ROGUE, "neulovimost"): B([
        "у умения нет активности: запрет преимущества против плута не исполняется",
    ]),
    (ROGUE, "ulybka_fortuny"): A([
        "движок принимает умение и тратит использование, но замена броска на 20 не наблюдается",
    ]),
    (ROGUE, "epicheskii_dar"): A([
        "дар не входит в репертуар ни одного класса: use_feature даёт CastFailed(not_in_repertoire)",
        "выгод дара нет: activities и passive_effects пусты в данных, слово epic в ядре встречается только в labels.py",
    ]),

    # ---------------- Следопыт ----------------
    (RANGER, "class_attributes"): V(("attributes", RANGER, 10)),
    (RANGER, "sotvorenie_zaklinanii"): V(("spellcasting", RANGER, ("spell:cure-wounds@ranger",), "wis")),
    (RANGER, "izbrannyi_vrag"): V(("combat", "favored_enemy")),
    (RANGER, "iskusnoe_vladenie_oruzhiem"): V(("mastery", RANGER, 1,
                                               ("longbow", "shortsword"))),
    (RANGER, "iskusnyi_issledovatel"): B([
        "у умения нет ни активности, ни поля листа: языки и владения Искусного исследователя не появляются",
    ]),
    (RANGER, "boevoi_stil"): V(("style", RANGER, 2), handler="CombatEngine.attack"),
    (RANGER, "podklass_sledopyta"): V(("subclass", RANGER, "hunter")),
    (RANGER, "uvelichenie_harakteristik"): V(("asi", RANGER, 4)),
    (RANGER, "dopolnitelnaya_ataka"): V(("extra", RANGER, 5, 1)),
    (RANGER, "vseprolaznost"): B([
        "у умения нет активности: скорость лазания и плавания не появляется",
    ]),
    (RANGER, "ekspertnost"): V(("expertise", RANGER, 9, "stealth")),
    (RANGER, "neutomimyi"): B([
        "активность отвергается движком: `Unparseable dice expression: '1d8+(max(1,2))'` — формула лечения не разбирается",
    ]),
    (RANGER, "upornyi_ohotnik"): B([
        "у умения нет активности: сопротивление урону отмеченной добычи не исполняется",
    ]),
    (RANGER, "prirodnaya_zavesa"): V(("effect", RANGER, 14, "natures-veil", "ConditionApplied")),
    (RANGER, "metkii_ohotnik"): B([
        "у умения нет активности: преимущество против отмеченной добычи не исполняется",
    ]),
    (RANGER, "dikie_chuvstva"): B([
        "у умения нет активности: слепое зрение на 30 футов не появляется",
    ]),
    (RANGER, "groza_vragov"): V(("effect", RANGER, 20, "foe-slayer", "DamageApplied")),
    (RANGER, "epicheskii_dar"): A([
        "дар не входит в репертуар ни одного класса: use_feature даёт CastFailed(not_in_repertoire)",
        "выгод дара нет: activities и passive_effects пусты в данных, слово epic в ядре встречается только в labels.py",
    ]),

    # ---------------- Чародей ----------------
    (SORCERER, "class_attributes"): V(("attributes", SORCERER, 6)),
    (SORCERER, "sotvorenie_zaklinanii"): V(("spellcasting", SORCERER, ("cantrip:fire-bolt@sorcerer", "spell:magic-missile@sorcerer"), "cha")),
    (SORCERER, "vrozhdennoe_charodeistvo"): V(("combat", "innate_sorcery")),
    (SORCERER, "istochnik_magii"): A([
        "движок принимает умение и тратит использование, но обмен очков на ячейки и обратно прогоном не наблюдается",
    ]),
    (SORCERER, "metamagiya"): B([
        "Метамагия остаётся прозой: у умения нет типизированной активности (матрица апстрима: Metamagic inert)",
    ]),
    (SORCERER, "podklass_charodeya"): V(("subclass", SORCERER, "draconic")),
    (SORCERER, "uvelichenie_harakteristik"): V(("asi", SORCERER, 4)),
    (SORCERER, "charodeiskoe_vosstanovlenie"): V(("combat", "sorcerous_restoration")),
    (SORCERER, "voploshenie_charodeistva"): A([
        "движок принимает умение, но прибавка к метамагии не наблюдается",
    ]),
    (SORCERER, "misticheskii_apofeoz"): B([
        "у умения нет активности: снятие предела очков магии не исполняется",
    ]),
    (SORCERER, "akkuratnoe_zaklinanie"): B([
        "вариант метамагии «Аккуратное заклинание» не исполняется: метамагия в движке — проза",
    ]),
    (SORCERER, "epicheskii_dar"): A([
        "дар не входит в репертуар ни одного класса: use_feature даёт CastFailed(not_in_repertoire)",
        "выгод дара нет: activities и passive_effects пусты в данных, слово epic в ядре встречается только в labels.py",
    ]),
}

PROVEN = {key: row for key, row in CATALOG.items() if row["proof"] is not None}


# --------------------------------------------------------------------------
# Чтение карты
# --------------------------------------------------------------------------
def map_rules():
    """(класс, ключ) -> запись карты из рабочего JSON."""
    rows = {}
    for path in sorted(CHAPTER.glob("*.json")):
        slug = SLUG_BY_FILE[path.name]
        for row in json.loads(path.read_text(encoding="utf-8"))["rules"]:
            rows[(slug, row["id"].split(".")[3])] = row
    return rows


MAP = map_rules()


# --------------------------------------------------------------------------
# Проверки
# --------------------------------------------------------------------------
def test_the_map_has_exactly_the_201_rules_of_the_catalog():
    assert len(MAP) == 201, len(MAP)
    assert set(MAP) == set(CATALOG), (set(MAP) ^ set(CATALOG))


def test_every_status_comes_from_the_closed_enum():
    for key, row in MAP.items():
        readiness = row["readiness"]
        assert readiness["status"] in STATUSES, (key, readiness["status"])
        assert row["implementation"] in IMPLEMENTATIONS, (key, row["implementation"])
        assert row["auto_activate"] is False, key


def test_no_rule_claims_execution_without_a_reason():
    """`blocked` и `adjudication_required` обязаны назвать дыру; `verified` — нет."""
    for key, row in MAP.items():
        status = row["readiness"]["status"]
        blockers = row["readiness"]["blockers"]
        if status == "engine_subset_verified":
            assert blockers == [], (key, blockers)
            assert row["implementation"] == "existing_engine_subset", key
        else:
            assert blockers and all(block.strip() for block in blockers), (key, blockers)
        if status == "blocked":
            assert row["readiness"]["handler"] is None, key
            assert row["implementation"] == "not_implemented", key


def test_the_map_matches_the_evidence_table():
    """Второй источник: каталог этого файла против рабочего JSON."""
    for key, expected in CATALOG.items():
        row = MAP[key]
        assert row["readiness"]["status"] == expected["status"], (key, row["readiness"]["status"])
        assert row["implementation"] == expected["implementation"], key
        assert tuple(row["readiness"]["blockers"]) == expected["blockers"], key
        assert row["readiness"]["handler"] == expected["handler"], (key, row["readiness"]["handler"])


@pytest.mark.parametrize("key", sorted(PROVEN), ids=lambda key: f"{key[0]}-{key[1]}")
def test_a_verified_rule_really_executes(key):
    run_proof(PROVEN[key]["proof"])


def test_a_rule_without_execution_has_no_proof():
    for key, row in MAP.items():
        if row["readiness"]["status"] != "engine_subset_verified":
            assert key not in PROVEN, f"{key}: статус не «исполняется», а доказательство заявлено"


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("level", LEVELS)
def test_all_twelve_classes_derive_a_sheet_with_numbers(cls, level):
    sheet = sheet_of(cls, level)
    hp, features = EXPECTED_SHEET[cls][level]
    assert sheet["hp_max"] == hp, (cls, level, sheet["hp_max"])
    assert len(sheet["features"]) == features, (cls, level, len(sheet["features"]))
    assert sheet["hit_dice"], (cls, level)


def test_every_class_proves_one_feature_in_live_combat():
    """Минимум одно классовое умение на класс, применённое через use_feature."""
    applied = {
        "barbarian": "rage", "bard": "bardic", "cleric": "channel_divinity",
        "druid": "wild_shape", "fighter": "second_wind", "monk": "uncanny",
        "paladin": "lay_on_hands", "ranger": "favored_enemy", "rogue": "sneak_attack",
        "sorcerer": "innate_sorcery", "warlock": "magical_cunning",
        "wizard": "arcane_recovery",
    }
    assert sorted(applied) == CLASSES
    for cls, proof in applied.items():
        COMBAT[proof]()


def test_status_totals_read_as_a_sentence():
    counts = {"engine_subset_verified": 0, "adjudication_required": 0, "blocked": 0}
    for row in MAP.values():
        counts[row["readiness"]["status"]] += 1
    assert sum(counts.values()) == 201
    assert counts["engine_subset_verified"] > 60, counts
    assert counts["blocked"] < 80, counts
    print("\nИТОГО r131:", counts)
