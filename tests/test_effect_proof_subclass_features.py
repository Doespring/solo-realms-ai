"""r170: умение подкласса при нажатии либо меняет состояние, либо честно объясняет.

Карточка r169 принесла в лист 41 умение девяти подклассов (четыре коллегии
Барда + по одному подклассу Варвара, Воина, Волшебника, Жреца, Плута). Часть из
них в пине — только дословный текст, часть — активность-утилита без `effects`,
часть — формула лечения со `@scaling`. До r170 нажатие в этих случаях давало
либо «ничего», либо внутреннюю ошибку движка, а урон-активность без цели била
самого героя.

Этот файл держит два обещания сразу:

* у каждого умения есть план (`solo.nat20_content.feature_plan`) одного из
  четырёх видов: `native` (одна типизированная активность меняет состояние),
  `choice` (вариантов несколько, выбирает игрок), `master` (типизированной
  активности нет — решает Мастер), `blocked` (активность есть, движок пина её
  не разрешает; причина названа и правка нужна в вендоре);
* обещание плана совпадает с поведением движка: `native` действительно меняет
  состояние в живом бою, `choice` без выбора варианта отказывает, `master` и
  `blocked` дают названный отказ, а не тишину.

Класс проблемы в отчёте: **A** — типизированная активность есть, эффект
наблюдается (чинилось в хосте); **B** — активности нет, запись «решает Мастер»
с причиной; **C** — нужна правка вендора, статус `blocked` с точной ошибкой.

Данные подклассов PHB 2024 приватные (в git не идут). Без каталога
`integration/nat20/phb-2024/subclasses` их 12 умений в таблицу не попадают:
чистый клон остаётся зелёным, а проверка покрытия говорит об этом прямо.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance, derive_sheet
from dnd5e_engine.specs import EncounterMemberSpec, GridScene

from solo.nat20_canonical import PHB_ROOT, PhbSubclassLoader
from solo.nat20_content import (FEATURE_PLAN_STATUSES, feature_intent, feature_plan,
                                is_subclass_feature)

SCORES = AbilityScores(strength=13, dexterity=13, constitution=13,
                       intelligence=13, wisdom=13, charisma=13)
LOADER = PhbSubclassLoader()

#: Девять подклассов карточки: четыре коллегии Барда и по одному подклассу
#: остальных пяти классов.
SUBCLASSES = (
    ("barbarian", "berserker"),
    ("fighter", "champion"),
    ("wizard", "evoker"),
    ("cleric", "life"),
    ("rogue", "thief"),
    ("bard", "lore"),
    ("bard", "phb2024.bard.valor"),
    ("bard", "phb2024.bard.glamour"),
    ("bard", "phb2024.bard.dance"),
)
LEVEL = 20  # все умения подкласса на месте; выше уровня подкласса умений нет
needs_phb = pytest.mark.skipif(
    not PHB_ROOT.is_dir(),
    reason="нет integration/nat20/phb-2024/subclasses (приватные данные книги): "
           "собрать scripts/build_phb_subclasses.py",
)


# --------------------------------------------------------------------------
# Стенд: живой бой и проекция состояния
# --------------------------------------------------------------------------
def spec(cls, subclass, level=LEVEL, with_subclass=True):
    return CharacterBuildSpec(species_slug="human", background_slug="soldier", class_slug=cls,
                              classes={cls: level}, level=level, ability_scores=SCORES,
                              equipment=("longsword", "shield", "chain-mail"),
                              subclass_slug=subclass if with_subclass else None)


def sheet_features(cls, subclass):
    return list(derive_sheet(spec(cls, subclass), loader=LOADER).features)


def subclass_features(cls, subclass):
    """Только умения подкласса: базовые умения класса отсекаются по листу без него."""
    base = set(derive_sheet(spec(cls, subclass, with_subclass=False), loader=LOADER).features)
    return [slug for slug in sheet_features(cls, subclass)
            if slug not in base and is_subclass_feature(slug)]


def combat(cls, subclass):
    """Герой 60/200 HP, раненый союзник 10/60 и противник: видно и урон, и лечение."""
    party = [build_party_member(spec(cls, subclass),
                                CombatInstance(entity_id="pc1", name="PC", zone_id="0,0",
                                               initiative=20, hp_max=200, hp_current=60),
                                loader=LOADER),
             build_party_member(spec("fighter", None, level=1, with_subclass=False),
                                CombatInstance(entity_id="pc2", name="ALLY", zone_id="0,1",
                                               initiative=18, hp_max=60, hp_current=10),
                                loader=LOADER)]
    handle = asyncio.run(O.start_combat(
        session_id="r170-subclass-features", party=party,
        encounter=[EncounterMemberSpec(entity_id="m1", name="Foe", entity_type="Monster",
                                       initiative=1, hp_current=500, hp_max=500, ac=12,
                                       zone_id="1,0")],
        grid_scene=GridScene(width=9, height=7), rng_seed=5)).handle
    O.drain_pending_events(handle)
    return handle


def state(handle):
    live = O.get_live(handle)
    return dict(hp=dict(live.tracked_hp),
                counters={key: {name: dict(row) for name, row in value.items()}
                          for key, value in live.custom_counters_by_entity.items() if value},
                conditions={key: sorted(value) for key, value in live.active_conditions.items()
                            if value})


def use(handle, slug, **intent):
    """Один `use_feature`; отказ движка возвращается строкой, а не падением."""
    try:
        asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(
            intent_type="use_feature", feature_id=slug, **intent)))
    except Exception as error:  # noqa: BLE001 - отказ движка и есть наблюдаемое поведение
        return f"Rejected:{type(error).__name__}:{error}", []
    return "", [event for event in O.drain_pending_events(handle)]


def event_names(events):
    return {type(event).__name__ for event in events}


def failed_reason(events):
    for event in events:
        if type(event).__name__.endswith("Failed"):
            return str(getattr(event, "reason", "") or "")
    return ""


#: Таблица умений: (класс, подкласс, слаг). Строится из листа, не из переписи,
#: поэтому новое умение в данных обязано получить план и попасть под стражу.
FEATURES = []
for _cls, _subclass in SUBCLASSES:
    if _subclass.startswith("phb2024.") and not PHB_ROOT.is_dir():
        continue
    FEATURES += [(_cls, _subclass, slug) for slug in subclass_features(_cls, _subclass)]

FEATURE_IDS = [f"{cls}/{subclass}/{slug}" for cls, subclass, slug in FEATURES]


def _params(*statuses):
    """Параметры только для умений с названным статусом плана."""
    rows, ids = [], []
    for cls, subclass, slug in FEATURES:
        plan = feature_plan(slug) or {}
        if plan.get("status") in statuses:
            rows.append((cls, subclass, slug))
            ids.append(f"{subclass}/{slug}")
    return pytest.mark.parametrize("cls,subclass,slug", rows, ids=ids)


# --------------------------------------------------------------------------
# 1. У каждого умения подкласса есть план с причиной
# --------------------------------------------------------------------------
@pytest.mark.parametrize("cls,subclass,slug", FEATURES, ids=FEATURE_IDS)
def test_every_subclass_feature_has_an_honest_plan(cls, subclass, slug):
    plan = feature_plan(slug)
    assert plan is not None, slug
    assert plan["status"] in FEATURE_PLAN_STATUSES, (slug, plan["status"])
    assert is_subclass_feature(slug), slug
    if plan["status"] in ("master", "blocked"):
        # «Решает Мастер» без причины — та же тишина, только вежливая.
        assert plan["reason"].strip(), (slug, plan)
        with pytest.raises(ValueError) as refusal:
            feature_intent(slug)
        assert plan["reason"][:30] in str(refusal.value), (slug, str(refusal.value))
    if plan["status"] == "native":
        assert plan["activity_id"], slug
        # Цель называется только у бьющей активности: иначе лечение уходит чужому.
        assert plan["needs_target"] == (plan["kind"] == "damage"), (slug, plan)
    if plan["status"] == "choice":
        assert len(plan["activities"]) > 1, slug


# --------------------------------------------------------------------------
# 2. A: native — нажатие меняет состояние (было → стало)
# --------------------------------------------------------------------------
@_params("native")
def test_native_feature_changes_state(cls, subclass, slug):
    plan = feature_plan(slug)
    handle = combat(cls, subclass)
    before = state(handle)
    prepared = feature_intent(slug, target_id="m1" if plan["needs_target"] else None)
    assert prepared["target_id"] == ("m1" if plan["needs_target"] else None), prepared
    error, events = use(handle, slug, **{key: value for key, value in prepared.items()
                                         if key != "feature_id" and value})
    assert not error, (slug, error)
    after = state(handle)
    names = event_names(events)
    kind = plan["kind"]
    if kind == "damage":
        assert after["hp"]["m1"] < before["hp"]["m1"], (slug, before["hp"], after["hp"])
        assert "DamageApplied" in names, names
    elif kind == "heal":
        healed = [entity for entity in after["hp"]
                  if after["hp"][entity] > before["hp"][entity] and after["hp"][entity] > 0]
        assert healed, (slug, before["hp"], after["hp"])
        assert "HealingApplied" in names, names
    elif kind == "save":
        # Спасбросок — это бросок, который видит таблица; трата применения — счётчик.
        assert "SaveRolled" in names, names
        spent = after["counters"].get("pc1", {}).get(f"feature_use:{slug}", {}).get("spent")
        assert spent == 1, (slug, after["counters"])
    elif kind == "check":
        assert "CheckRolled" in names, names
    else:  # pragma: no cover - новый вид активности обязан получить свою проверку
        raise AssertionError(f"{slug}: у вида «{kind}» нет проверки наблюдаемого эффекта")


# --------------------------------------------------------------------------
# 3. A: choice — вариантов несколько, хост спрашивает, а не угадывает
# --------------------------------------------------------------------------
@_params("choice")
def test_choice_feature_offers_variants_and_refuses_to_guess(cls, subclass, slug):
    plan = feature_plan(slug)
    with pytest.raises(ValueError, match="предлагает варианты"):
        feature_intent(slug)
    usable = [row for row in plan["activities"] if row["status"] == "native"]
    assert usable, (slug, plan["activities"])
    for variant in plan["activities"]:
        if variant["status"] == "native":
            continue
        # Неисполнимый вариант обязан отказывать своей причиной, а не тишиной.
        assert variant["reason"].strip(), (slug, variant)
        with pytest.raises(ValueError) as refusal:
            feature_intent(slug, activity_id=variant["activity_id"])
        assert variant["reason"][:30] in str(refusal.value), (slug, variant, str(refusal.value))
    for variant in usable:
        handle = combat(cls, subclass)
        before = state(handle)
        prepared = feature_intent(slug, activity_id=variant["activity_id"],
                                  target_id="m1" if variant["needs_target"] else None)
        error, events = use(handle, slug, activity_id=prepared["activity_id"],
                            **({"target_id": prepared["target_id"]} if prepared["target_id"] else {}))
        assert not error, (slug, variant["activity_id"], error)
        after = state(handle)
        names = event_names(events)
        if variant["kind"] == "damage":
            assert after["hp"]["m1"] < before["hp"]["m1"], (slug, variant, before["hp"], after["hp"])
        elif variant["kind"] == "check":
            assert "CheckRolled" in names, (slug, variant, names)
        elif variant["kind"] == "save":
            assert "SaveRolled" in names, (slug, variant, names)
        elif variant["kind"] == "heal":
            assert any(after["hp"][e] > before["hp"][e] for e in after["hp"]), (slug, variant)
        elif variant["kind"] == "utility":
            # Утилита со своим запасом применений: наблюдаемое — трата.
            spent = after["counters"].get("pc1", {}).get(f"feature_use:{slug}", {}).get("spent")
            assert spent, (slug, variant, after["counters"])
        else:  # pragma: no cover
            raise AssertionError(f"{slug}: вид «{variant['kind']}» без проверки")


# --------------------------------------------------------------------------
# 4. B и C: «решает Мастер» и «нужен вендор» — отказ с причиной, а не тишина
# --------------------------------------------------------------------------
@_params("master")
def test_master_feature_names_the_master_and_the_engine_answer(cls, subclass, slug):
    plan = feature_plan(slug)
    with pytest.raises(ValueError) as refusal:
        feature_intent(slug)
    assert plan["reason"][:30] in str(refusal.value), (slug, str(refusal.value))
    # Движок отвечает ровно тем, что названо в причине: умения нет в репертуаре
    # или у него нет типизированной активности.
    handle = combat(cls, subclass)
    error, events = use(handle, slug)
    assert not error, (slug, error)
    assert failed_reason(events) in {"feature_not_executable", "not_in_repertoire"}, (
        slug, failed_reason(events), event_names(events))
    assert plan["source"] == ("phb-2024-ru" if slug.startswith("phb2024.") else "pin")


@_params("blocked")
def test_blocked_feature_names_the_vendor_gap_and_reproduces_it(cls, subclass, slug):
    plan = feature_plan(slug)
    with pytest.raises(ValueError) as refusal:
        feature_intent(slug)
    assert plan["reason"][:30] in str(refusal.value), (slug, str(refusal.value))
    reason = plan["reason"]
    handle = combat(cls, subclass)
    before = state(handle)
    activity = (plan.get("activity_id")
                or next((row["activity_id"] for row in plan["activities"]
                         if row["status"] == "blocked"), None))
    error, events = use(handle, slug, **({"activity_id": activity} if activity else {}))
    after = state(handle)
    if "@scaling" in reason:
        # Точная ошибка вендора: движок не подставляет @scaling у умений.
        assert "Unhandled roll-data token: '@scaling'" in error, (slug, error)
    elif "пассивном изменении" in reason:
        assert failed_reason(events) == "feature_not_executable", (slug, event_names(events))
        assert after["hp"] == before["hp"], (slug, after["hp"])
        # Пассив, который замерен как невидимый, не должен всплыть в бою
        # состояниями: иначе «заблокировано» было бы ложью.
        assert after["conditions"] == before["conditions"], (slug, before["conditions"],
                                                             after["conditions"])
    elif "урон-часть активности без костей" in reason:
        assert after["hp"] == before["hp"], (slug, before["hp"], after["hp"])
    elif "тратит чужой запас" in reason:
        # Замер: нажатие увеличивает применение ЧУЖОГО умения (Ярость), а своё
        # применение не возвращает — состояние меняется не туда.
        assert not error, (slug, error)
        grown = {name: row for name, row in after["counters"].get("pc1", {}).items()
                 if name != f"feature_use:{slug}"
                 and row.get("spent", 0) > before["counters"].get("pc1", {}).get(name, {}).get("spent", 0)}
        assert grown, (slug, before["counters"], after["counters"])
    else:
        # Утилита без effects: движок принимает интент и не меняет ничего —
        # именно эту тишину и закрывает хост, отказывая до отправки.
        assert not error, (slug, error)
        assert "IntentSubmitted" in event_names(events), (slug, event_names(events))
        assert after == before, (slug, before, after)


# --------------------------------------------------------------------------
# 5. Страж таблицы: ни одно умение подкласса не выпало из прогона
# --------------------------------------------------------------------------
def test_the_table_covers_every_granted_subclass_feature():
    covered = {(cls, subclass, slug) for cls, subclass, slug in FEATURES}
    missing = []
    for cls, subclass in SUBCLASSES:
        if subclass.startswith("phb2024.") and not PHB_ROOT.is_dir():
            continue
        for slug in subclass_features(cls, subclass):
            if (cls, subclass, slug) not in covered:
                missing.append((cls, subclass, slug))
    assert not missing, missing
    # Число умений этих девяти подклассов на 20 уровне замерено прогоном:
    # 29 записей пина (SRD) + 12 умений книги. Дрейф данных обязан быть слышен.
    assert len(covered) == (41 if PHB_ROOT.is_dir() else 29), len(covered)


def test_counts_are_printable_and_nonempty():
    """Числа для отчёта: сколько умений эффект дают, сколько записаны на Мастера."""
    plans = {slug: (feature_plan(slug) or {}) for _cls, _subclass, slug in FEATURES}
    kinds = {slug: plan.get("status") for slug, plan in plans.items()}
    assert all(kinds.values()), [slug for slug, status in kinds.items() if not status]
    native = [slug for slug, status in kinds.items() if status == "native"]
    choice = [slug for slug, status in kinds.items() if status == "choice"]
    master = [slug for slug, status in kinds.items() if status == "master"]
    blocked = [slug for slug, status in kinds.items() if status == "blocked"]
    assert native and blocked and master, kinds
    # Не тавтология: чистые числа из отчёта карточки. Их рост или падение —
    # повод переписать отчёт, а не «зелёный прогон».
    if PHB_ROOT.is_dir():
        assert (len(native), len(choice), len(master), len(blocked)) == (4, 3, 23, 11), (
            native, choice, master, blocked)
    print(f"\nr170: умений подклассов {len(kinds)}; эффект (A) native {len(native)}, "
          f"с выбором {len(choice)}; решает Мастер (B) {len(master)}; "
          f"упирается в вендор (C) {len(blocked)}")
    print("A:", ", ".join(sorted(native + choice)))
    print("B:", ", ".join(sorted(master)))
    print("C:", ", ".join(sorted(blocked)))


def test_save_activities_keep_the_target_the_caller_named():
    """Спасбросок — не площадка по умолчанию: цель не выбрасывается.

    Дефект, найденный критиком r170: сначала хост снимал цель у всех
    несбивающих активностей, и «Завершающая вибрация» (quivering-palm) или
    «Низвержение в ад» (hurl-through-hell) оставались с одним
    `IntentSubmitted`. Здесь страж на сам контракт: у активность-спасброска
    названная цель возвращается нетронутой.
    """
    plan = feature_plan("quivering-palm")
    assert plan["status"] == "choice", plan
    save_variant = next(row for row in plan["activities"] if row["kind"] == "save")
    prepared = feature_intent("quivering-palm", activity_id=save_variant["activity_id"],
                              target_id="m1")
    assert prepared["target_id"] == "m1", prepared


def test_phb_colleges_are_in_the_table_when_the_book_data_is_present():
    """Без приватных данных 12 умений книги честно отсутствуют, а не «зелёные»."""
    phb = [row for row in FEATURES if row[1].startswith("phb2024.")]
    if not PHB_ROOT.is_dir():
        assert not phb
        pytest.skip("нет integration/nat20/phb-2024/subclasses")
    assert len(phb) == 12, [(subclass, slug) for _cls, subclass, slug in phb]
    for _cls, _subclass, slug in phb:
        plan = feature_plan(slug)
        assert plan["source"] == "phb-2024-ru", slug
        assert plan["status"] == "master", (slug, plan["status"])


# --------------------------------------------------------------------------
# 6. Хост целиком: нажатие из кампании (worker) даёт эффект или отказ с причиной
# --------------------------------------------------------------------------
def host_campaign(tmp_path, subclass="berserker", level=6):
    """Живая экспериментальная кампания: бой с крысой на соседней клетке."""
    from solo.game_commands import GameCommand
    from solo.nat20_api import Nat20GameEngine
    from solo.storage import Store

    from nat20_builds import legal

    store = Store(tmp_path / "r170-host.sqlite3")
    api = Nat20GameEngine(store)
    build = legal(class_slug="barbarian", species_slug="human", level=level,
                  subclass_slug=subclass, equipment=["greataxe"])

    def run(identity, **action):
        return api.execute(api_cid[0], GameCommand(
            request_id=identity, expected_version=store.campaign(api_cid[0])["version"],
            action=action))

    api_cid = [api.create_experimental_campaign(
        "Berserker r170", build=build,
        encounters={"rat": dict(grid=dict(width=9, height=6), hero_position=dict(x=1, y=1, z=0),
                                actors=[dict(id="rat-a", monster="rat", position=dict(x=2, y=1, z=0),
                                             attack_bonus=2, attack_bonus_source=dict(
                                                 kind="authored_scenario", id="r170", version="1",
                                                 note="test input"))])},
        seed=5)["id"]]
    run("battle", kind="start_combat", encounter_id="rat")
    view = store.campaign(api_cid[0])["state"]["nat20"]["combat"]["view"]
    if view["initiative"][view["current_turn_index"]]["entity_id"] != "hero":
        run("turn", kind="end_turn")
    return store, api, api_cid[0], run


def test_host_path_makes_a_subclass_feature_bite(tmp_path):
    """Через worker: «Ярость берсерка» бьёт цель, а не молчит.

    Было → стало: `native_action/use_feature frenzy` без плана хоста давал
    только `IntentSubmitted` (хосту нечего было передать в activity_id/target);
    с планом r170 воркер сам ставит activity_id и цель, и крыса получает урон.
    """
    store, _api, cid, run = host_campaign(tmp_path)
    before = dict(store.campaign(cid)["state"]["nat20"]["combat"]["view"]["tracked_hp"])
    assert before["rat-a"] > 0, before
    result = run("frenzy", kind="native_action", operation="use_feature",
                 feature_id="frenzy", target_id="rat-a")
    kinds = [event["type"] for event in result["native_events"]]
    assert "damage_applied" in kinds, kinds
    after = store.campaign(cid)["state"]["nat20"]["combat"]["view"]["tracked_hp"]
    assert after["rat-a"] < before["rat-a"], (before, after)


def test_host_path_refuses_a_dead_subclass_feature_with_its_reason(tmp_path):
    """Та же кнопка у мёртвого умения: отказ с причиной, а не «ничего».

    «Возмездие» (Retaliation) — активность-утилита без effects: движок принял
    бы интент и не изменил состояние. Хост отказывает до отправки и называет
    причину, поэтому на доске игрока видно, что умение ведёт не движок.
    """
    _store, _api, _cid, run = host_campaign(tmp_path)
    plan = feature_plan("retaliation")
    assert plan["status"] == "blocked"
    with pytest.raises(ValueError) as refusal:
        run("retaliation", kind="native_action", operation="use_feature",
            feature_id="retaliation")
    assert plan["reason"][:30] in str(refusal.value), str(refusal.value)
