"""r175: восемь умений базовых классов доходят до игры, а не до отказа движка.

r171 разложил 56 правил главы классов: у восьми эффект в движке уже есть, а до
игры правило не доходило, потому что хост не проверял условие, не спрашивал
выбор и не подставлял параметры интента. Этот файл держит по каждому умению три
обещания сразу:

1. **эффект**: разрешённый ход меняет состояние (счётчик применения, эффект
   `EffectApplied`/`ConditionApplied`, временные хиты, форма, урон);
2. **отказ**: хост отказывает ДО интента и называет причину, когда условие
   правила не выполнено (тяжёлый доспех, не-нежить, дальше 30/60 футов, нет
   выбранного варианта или зверя) — вместо `CastFailed` движка;
3. **идемпотентность**: повтор того же хода не тратит ресурс дважды.

Хост-путь — это `Nat20GameEngine.execute` → `_preflight` → `nat20_worker`: так
ходят и экран, и чат. Два места проверяются на уровне движка, и это названо
прямо, а не выдано за хост-путь:

* **Бардовское вдохновение** — в кампании хоста в бою один герой (второго
  члена партии хост не создаёт), поэтому давать кость некому; хост-путь и
  эффект проверяются на проекции боя с двумя героями, а отказ «нет союзника» —
  на настоящей кампании. Именно из-за этого правило остаётся
  `adjudication_required` с названным блокером (`в бою кампании один герой`);
* **состояния от Изгнания нежити** зависят от броска спасброска, поэтому
  провал снимается прогоном по зёрнам на движке, а хост-путь (счётчик
  применения и отказ по типу цели) — на кампании.

Хост-проверки живут в `solo.nat20_content.feature_gate` / `class_feature_choices`
и вызываются из `Nat20GameEngine._preflight` (общий вход исполнения и экрана) и
из `solo.nat20_screen` (кнопки). Замеры, на которых стоят проверки:
`reports/r171-classes-adjudication.md` и докстроки самих функций.

Функции `proof_*` отдаются карте доказательств r131
(`tests/test_class_coverage_proof.py`, вид `r175`), поэтому вызываются и с
`tmp_path`, и без корня — тогда создают свой временный каталог.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import socket
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from dnd5e_engine import orchestrator as O  # noqa: E402
from dnd5e_engine.build_party import build_party_member  # noqa: E402
from dnd5e_engine.build_spec import (AbilityScores, CharacterBuildSpec, CombatInstance,  # noqa: E402
                                     derive_sheet)
from dnd5e_engine.lib_loader import get_lib_loader  # noqa: E402
from dnd5e_engine.specs import EncounterMemberSpec, GridScene  # noqa: E402

from nat20_builds import legal  # noqa: E402
from solo.game_commands import GameCommand, NativeAction  # noqa: E402
from solo.nat20_api import Nat20GameEngine  # noqa: E402
from solo.nat20_content import (UNARMED_STRIKE, _beasts, _loader_key,  # noqa: E402
                                class_feature_choices, feature_gate, known_shapes,
                                monks_focus_options, turn_undead_activity)
from solo.nat20_screen import Nat20Screen  # noqa: E402
from solo.nat20_worker import plain  # noqa: E402
from solo.storage import Store  # noqa: E402

SCORES = AbilityScores(strength=16, dexterity=14, constitution=14,
                       intelligence=10, wisdom=13, charisma=10)
FLURRY = "2ghJTBhilLrFn9xT"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("SOLO_TEST", "1")
    connect = socket.socket.connect

    def local(sock, address):
        if address[0] in ("127.0.0.1", "::1"):
            return connect(sock, address)
        raise AssertionError("External calls are forbidden in this test")

    monkeypatch.setattr(socket.socket, "connect", local)


@contextmanager
def workspace(root=None):
    if root is not None:
        yield Path(root)
        return
    with tempfile.TemporaryDirectory(prefix="r175-") as path:
        yield Path(path)


# --------------------------------------------------------------------------
# Хост-путь кампании: создание, бой, команда, состояние
# --------------------------------------------------------------------------
def scene(monsters, spots, grid=(12, 8)):
    return dict(grid=dict(width=grid[0], height=grid[1]),
                hero_position=dict(x=1, y=1, z=0),
                actors=[dict(id="foe%d" % index, monster=slug,
                             position=dict(x=x, y=y, z=0))
                        for index, (slug, (x, y)) in enumerate(zip(monsters, spots))])


def campaign(root, name, cls, level, *, subclass=None, monsters=("wolf",),
             spots=((2, 1),), equipment=("dagger",), seed=5):
    api = Nat20GameEngine(Store(Path(root) / (name + ".sqlite3")))
    build = legal(class_slug=cls, species_slug="human", level=level,
                  equipment=list(equipment), subclass_slug=subclass)
    cid = api.create_experimental_campaign(
        name, build=build, encounters={"yard": scene(monsters, spots)},
        seed=seed, title="Двор", description="двор")["id"]
    return api, cid


def runtime(api, cid):
    return api.store.campaign(cid)["state"]["nat20"]


def execute(api, cid, identity, **action):
    """Команда тем же путём, что и ход игрока; отказ возвращается исключением."""
    return api.execute(cid, GameCommand(request_id=identity,
                                        expected_version=api.store.campaign(cid)["version"],
                                        action=action))


def refusal(api, cid, identity, **action):
    """Отказ хоста/движка строкой и версия до/после — состояние не двигается."""
    before = api.store.campaign(cid)["version"]
    with pytest.raises(ValueError) as caught:
        execute(api, cid, identity, **action)
    assert api.store.campaign(cid)["version"] == before, "отказ не должен двигать состояние"
    return str(caught.value)


def gate(api, cid, **action):
    """Причина отказа хост-проверки или None, если условие выполнено."""
    typed = NativeAction.model_validate(dict(kind="native_action", **action))
    try:
        feature_gate(runtime(api, cid), typed)
    except ValueError as error:
        return str(error)
    return None


def spent(api, cid, counter):
    return (runtime(api, cid)["hero"].get("custom_counters") or {}).get(counter, {}).get("spent")


def hero_view(api, cid):
    view = runtime(api, cid)["combat"]["view"]
    return next(row for row in view["initiative"] if row["entity_id"] == "hero")


def event_types(result):
    return [event.get("type") for event in result.get("native_events") or ()]


# --------------------------------------------------------------------------
# Движок напрямую — только там, где у хоста нет второго героя в кампании
# и где нужен посев броска
# --------------------------------------------------------------------------
def party_member(cls, level, *, entity="hero", zone="0,0", initiative=20, hp=200,
                 equipment=("dagger",), counters=None, subclass=None):
    spec = CharacterBuildSpec(species_slug="human", background_slug="soldier",
                              class_slug=cls, classes={cls: level}, level=level,
                              ability_scores=SCORES, equipment=tuple(equipment),
                              subclass_slug=subclass)
    member = build_party_member(spec, CombatInstance(entity_id=entity, name=entity.upper(),
                                                     zone_id=zone, initiative=initiative,
                                                     hp_max=hp, hp_current=hp),
                                loader=get_lib_loader())
    if counters:
        member = member.model_copy(update={"custom_counters": counters})
    return member, derive_sheet(spec, loader=get_lib_loader()).features


def fight(party, foes, *, seed=5, width=12, height=8):
    encounter = [EncounterMemberSpec(entity_id=entity, name=entity.upper(),
                                     entity_type="Monster", initiative=1,
                                     hp_current=hp, hp_max=hp, ac=12, zone_id=zone,
                                     creature_type=kind)
                 for entity, zone, kind, hp in foes]
    handle = asyncio.run(O.start_combat(
        session_id="r175", party=party, encounter=encounter,
        grid_scene=GridScene(width=width, height=height), rng_seed=seed)).handle
    O.drain_pending_events(handle)
    return handle


def projection(handle, features, equipment=("dagger",)):
    """Проекция боя так, как её видит хост (`plain` — сериализатор воркера)."""
    live = O.get_live(handle)
    effects = [plain(effect) for entity in sorted(set(live.party_ids) | set(live.encounter_ids))
               for effect in O.get_actor_active_effects(handle, entity)]
    hero = next(row for row in live.initiative if row.entity_id == "hero")
    return dict(combat=dict(view=plain(live)),
                definition=dict(features=list(features)),
                hero=dict(classes=dict(hero.classes or {}),
                          equipment=tuple(equipment),
                          concentration_effect_id=hero.concentration_effect_id),
                active_effects=effects)


def send(handle, entity="hero", **intent):
    try:
        asyncio.run(O.submit_player_intent(handle, entity, O.PlayerIntent(**intent)))
    except Exception as error:  # noqa: BLE001 - отказ движка и есть наблюдаемое поведение
        return "REJECT:%s" % error, []
    return "", O.drain_pending_events(handle)


def names(events):
    return [type(event).__name__ for event in events]


def counters(handle, entity="hero"):
    return O.get_live(handle).custom_counters_by_entity.get(entity, {})


# --------------------------------------------------------------------------
# 1. Бардовское вдохновение
# --------------------------------------------------------------------------
def proof_vardo_vdohnovenie(root=None):
    with workspace(root) as tmp:
        # (а) в одиночной кампании давать кость некому — хост отказывает до интента
        api, cid = campaign(tmp, "bard-solo", "bard", 5, subclass="lore")
        execute(api, cid, "start", kind="start_combat", encounter_id="yard")
        reason = refusal(api, cid, "inspire-solo", kind="native_action",
                         operation="use_feature", feature_id="bardic-inspiration",
                         target_id="foe0")
        assert "нет союзника" in reason, reason
        assert spent(api, cid, "feature_use:bardic-inspiration") is None
        # (б) движок дал бы кость врагу: проверка «кому» стоит только в хосте
        bard, features = party_member("bard", 5, equipment=("dagger",))
        ally, _ = party_member("fighter", 1, entity="pc2", zone="1,1", initiative=18,
                               equipment=("longsword",))
        handle = fight([bard, ally], [("m1", "1,0", "humanoid", 500)])
        host = projection(handle, features, equipment=("dagger",))
        enemy = NativeAction(kind="native_action", operation="use_feature",
                             feature_id="bardic-inspiration", target_id="m1")
        with pytest.raises(ValueError, match="не союзник"):
            feature_gate(host, enemy)
        allowed = NativeAction(kind="native_action", operation="use_feature",
                               feature_id="bardic-inspiration", target_id="pc2")
        feature_gate(host, allowed)  # союзник в 5 фт, укрытия нет
        error, events = send(handle, intent_type="use_feature",
                             feature_id="bardic-inspiration", target_id="pc2")
        assert not error, error
        held = {effect.id for effect in O.get_actor_active_effects(handle, "pc2")}
        assert "effect:inspired" in held, held
        assert counters(handle)["feature_use:bardic-inspiration"]["spent"] == 1
        # (в) повтор не тратит второе применение
        error, again = send(handle, intent_type="use_feature",
                            feature_id="bardic-inspiration", target_id="pc2")
        assert counters(handle)["feature_use:bardic-inspiration"]["spent"] == 1, counters(handle)
        # (г) 65 футов — уже не 60: хост отказывает, движок бы дал
        far, _ = party_member("bard", 5, equipment=("dagger",))
        distant, _ = party_member("fighter", 1, entity="pc2", zone="13,1", initiative=18)
        distant_handle = fight([far, distant], [("m1", "1,0", "humanoid", 500)],
                               width=20, height=10)
        far_host = projection(distant_handle, features, equipment=("dagger",))
        with pytest.raises(ValueError, match="60 футов"):
            feature_gate(far_host, allowed)


# --------------------------------------------------------------------------
# 2. Ярость
# --------------------------------------------------------------------------
def proof_yarost(root=None):
    with workspace(root) as tmp:
        api, cid = campaign(tmp, "rage-heavy", "barbarian", 5, subclass="berserker",
                            equipment=("greataxe", "chain-mail"))
        execute(api, cid, "start", kind="start_combat", encounter_id="yard")
        reason = refusal(api, cid, "rage-heavy", kind="native_action",
                         operation="use_feature", feature_id="rage")
        assert "тяжёлом доспехе" in reason, reason
        assert spent(api, cid, "feature_use:rage") is None

        api, cid = campaign(tmp, "rage", "barbarian", 5, subclass="berserker",
                            equipment=("greataxe",))
        execute(api, cid, "start", kind="start_combat", encounter_id="yard")
        result = execute(api, cid, "rage", kind="native_action",
                         operation="use_feature", feature_id="rage")
        assert "effect_applied" in event_types(result), event_types(result)
        changes = result["native_events"][1]["effect"]["changes"]
        bonus = {row["key"]: row["value"] for row in changes}
        assert bonus["system.bonuses.mwak.damage"] == "+2", bonus
        assert spent(api, cid, "feature_use:rage") == 1
        # повтор: отказ с причиной, второе применение не тратится
        reason = refusal(api, cid, "rage-again", kind="native_action",
                         operation="use_feature", feature_id="rage")
        assert "уже действует" in reason, reason
        assert spent(api, cid, "feature_use:rage") == 1
        # заклинание под Яростью — отказ хоста (движок концентрацию не снимает)
        reason = refusal(api, cid, "rage-cast", kind="native_action",
                         operation="cast_spell", spell_id="bless", target_id="hero")
        assert "нельзя творить заклинания" in reason, reason


# --------------------------------------------------------------------------
# 3. Непрерывная ярость — дыры нет, нужен прогон двух боёв
# --------------------------------------------------------------------------
def proof_nepreryvnaya_yarost(root=None):
    del root  # движку нужны только счётчики, файлы не нужны
    barbarian, _ = party_member("barbarian", 15, equipment=("greataxe",),
                                counters={"feature_use:rage": {"spent": 1}})
    handle = fight([barbarian], [("m1", "1,0", "humanoid", 200)])
    live = counters(handle)
    assert live["feature_use:rage"]["spent"] == 0, live
    assert live["feature_use:persistent_rage_regained"]["spent"] == 1, live
    # второй бой в тот же день: маркер уже стоит — повторного восстановления нет
    again, _ = party_member("barbarian", 15, equipment=("greataxe",),
                            counters={"feature_use:rage": {"spent": 2},
                                      "feature_use:persistent_rage_regained": {"spent": 1}})
    handle = fight([again], [("m1", "1,0", "humanoid", 200)])
    assert counters(handle)["feature_use:rage"]["spent"] == 2, counters(handle)
    # без умения (14 уровень) счётчик не трогается: эффект даёт именно правило
    young, _ = party_member("barbarian", 14, equipment=("greataxe",),
                            counters={"feature_use:rage": {"spent": 1}})
    handle = fight([young], [("m1", "1,0", "humanoid", 200)])
    assert counters(handle)["feature_use:rage"]["spent"] == 1, counters(handle)


# --------------------------------------------------------------------------
# 4. Дикий облик
# --------------------------------------------------------------------------
def proof_dikii_oblik(root=None):
    with workspace(root) as tmp:
        api, cid = campaign(tmp, "druid", "druid", 5, subclass="land", monsters=("skeleton",))
        execute(api, cid, "start", kind="start_combat", encounter_id="yard")
        shapes = known_shapes(runtime(api, cid))
        # r207: известных форм ровно столько, сколько даёт книга (4/6/8), а не
        # весь бестиарий под предел Опасности. Отказ проверяем на звере,
        # который предел проходит, но в известные формы не входит, — берём его
        # из того же пула движка, а не литералом: эвристика выбора формы может
        # поменяться, а правило остаться.
        ids = [row["id"] for row in shapes]
        assert len(ids) == 6, ids
        eligible = {row["id"] for row in _beasts(_loader_key(get_lib_loader()), 5)}
        assert set(ids) < eligible, (ids, sorted(eligible))
        stranger = sorted(eligible - set(ids))[0]
        reason = refusal(api, cid, "shape-none", kind="native_action",
                         operation="use_feature", feature_id="wild-shape")
        assert "назовите известного зверя" in reason, reason
        assert any(row["id"] in reason for row in shapes), (reason, [row["id"] for row in shapes[:5]])
        reason = refusal(api, cid, "shape-bad", kind="native_action",
                         operation="use_feature", feature_id="wild-shape",
                         form_id="ancient-red-dragon")
        assert "недоступен на 5 уровне" in reason, reason
        # r207: зверь предел Опасности проходит, но в известные формы не
        # входит — отказ называет книгу и не расширяет список до бестиария
        reason = refusal(api, cid, "shape-not-known", kind="native_action",
                         operation="use_feature", feature_id="wild-shape",
                         form_id=stranger)
        assert "не входит в 6 известных форм" in reason, reason
        assert "Мастер вне хоста" in reason, reason
        assert spent(api, cid, "feature_use:wild-shape") is None

        form = ids[-1]  # старшая по Опасности из известных на 5 уровне
        result = execute(api, cid, "shape-known", kind="native_action",
                         operation="use_feature", feature_id="wild-shape", form_id=form)
        assert "temphp_applied" in event_types(result), event_types(result)
        view = runtime(api, cid)["combat"]["view"]
        assert view["tracked_temp_hp"]["hero"] == 5, view["tracked_temp_hp"]
        assert view["transformations"]["hero"]["form_slug"] == form, view["transformations"]
        assert spent(api, cid, "feature_use:wild-shape") == 1
        # повтор в том же ходу: Бонусное действие уже потрачено — второе применение не уходит
        refusal(api, cid, "shape-again", kind="native_action",
                operation="use_feature", feature_id="wild-shape", form_id=form)
        assert spent(api, cid, "feature_use:wild-shape") == 1
        # выход из формы: имя зверя не нужно, и хост это не блокирует
        assert any(parameters["kind"] == "native_action"
                   and parameters.get("feature_id") == "wild-shape"
                   and not parameters.get("form_id")
                   for _, parameters in class_feature_choices(runtime(api, cid))), \
            [label for label, _ in class_feature_choices(runtime(api, cid))]
        assert gate(api, cid, operation="use_feature", feature_id="wild-shape") is None
        # и он проходит исполнением: следующий свой ход — Бонусное действие свободно
        execute(api, cid, "wait", kind="end_turn")
        view = runtime(api, cid)["combat"]["view"]
        while view["initiative"][view["current_turn_index"]]["entity_id"] != "hero":
            execute(api, cid, "wait-again", kind="end_turn")
            view = runtime(api, cid)["combat"]["view"]
        execute(api, cid, "leave", kind="native_action", operation="use_feature",
                feature_id="wild-shape")
        assert runtime(api, cid)["combat"]["view"]["transformations"] == {}, \
            runtime(api, cid)["combat"]["view"]["transformations"]
        assert spent(api, cid, "feature_use:wild-shape") == 1, "выход не тратит применение"


# --------------------------------------------------------------------------
# 5. Изгнание нежити
# --------------------------------------------------------------------------
def proof_izgnanie_nezheti(root=None):
    with workspace(root) as tmp:
        api, cid = campaign(tmp, "cleric", "cleric", 5, subclass="life",
                            monsters=("skeleton", "goblin-warrior"),
                            spots=((2, 1), (1, 3)), equipment=("mace",))
        execute(api, cid, "start", kind="start_combat", encounter_id="yard")
        turn = turn_undead_activity()
        assert turn, "в данных умения нет активности Turn Undead"
        reason = refusal(api, cid, "turn-humanoid", kind="native_action",
                         operation="use_feature", feature_id="channel-divinity-cleric",
                         activity_id=turn, target_id="foe1")
        assert "не Нежить" in reason, reason
        assert spent(api, cid, "feature_use:channel-divinity-cleric") is None
        result = execute(api, cid, "turn-undead", kind="native_action",
                         operation="use_feature", feature_id="channel-divinity-cleric",
                         activity_id=turn, target_id="foe0")
        assert "save_rolled" in event_types(result), event_types(result)
        assert spent(api, cid, "feature_use:channel-divinity-cleric") == 1
        # провал спасброска вешает Испуганный и Ошеломлённый — прогон по зёрнам
        cleric, features = party_member("cleric", 5, equipment=("mace",))
        applied = set()
        for seed in (1, 2, 3):
            handle = fight([cleric], [("u1", "1,0", "undead", 100), ("m2", "0,1", "humanoid", 100)],
                           seed=seed)
            host = projection(handle, features)
            feature_gate(host, NativeAction(kind="native_action", operation="use_feature",
                                            feature_id="channel-divinity-cleric",
                                            activity_id=turn, target_id="u1"))
            error, events = send(handle, intent_type="use_feature",
                                 feature_id="channel-divinity-cleric",
                                 activity_id=turn, target_id="u1")
            assert not error, error
            applied |= set().union(*(O.get_live(handle).active_conditions.get(entity, set())
                                     for entity in ("u1",)))
        assert {"frightened", "incapacitated"} <= applied, applied
        # дальше 30 футов: цель есть, но правило её не достаёт
        far_api, far_cid = campaign(tmp, "cleric-far", "cleric", 5, subclass="life",
                                    monsters=("skeleton",), spots=((8, 1),),
                                    equipment=("mace",))
        execute(far_api, far_cid, "start", kind="start_combat", encounter_id="yard")
        reason = refusal(far_api, far_cid, "turn-far", kind="native_action",
                         operation="use_feature", feature_id="channel-divinity-cleric",
                         activity_id=turn, target_id="foe0")
        assert "дальше 30 футов" in reason, reason


# --------------------------------------------------------------------------
# 6. Боевые искусства
# --------------------------------------------------------------------------
def proof_boevye_iskusstva(root=None):
    with workspace(root) as tmp:
        api, cid = campaign(tmp, "monk", "monk", 5, subclass="hand",
                            monsters=("ogre",), equipment=("dagger",))
        execute(api, cid, "start", kind="start_combat", encounter_id="yard")
        assert gate(api, cid, operation="attack", weapon_id=UNARMED_STRIKE,
                    target_id="foe0") is None
        result = execute(api, cid, "unarmed", kind="native_action", operation="attack",
                         weapon_id=UNARMED_STRIKE, target_id="foe0")
        assert "damage_applied" in event_types(result), event_types(result)
        # бонусный безоружный удар: Бонусное действие уходит, бюджет атак — нет
        before = hero_view(api, cid)
        assert before["bonus_action_available"] is True
        result = execute(api, cid, "unarmed-bonus", kind="native_action", operation="attack",
                         weapon_id=UNARMED_STRIKE, target_id="foe0", use_bonus_action=True)
        assert "damage_applied" in event_types(result), event_types(result)
        after = hero_view(api, cid)
        assert after["bonus_action_available"] is False, after
        assert after["attacks_remaining"] == before["attacks_remaining"], (before, after)
        # послабление узкое: чужое оружие по-прежнему не «своё»
        reason = refusal(api, cid, "borrowed", kind="native_action", operation="attack",
                         weapon_id="greataxe", target_id="foe0")
        assert "not owned" in reason, reason


# --------------------------------------------------------------------------
# 7. Монашеская сосредоточенность
# --------------------------------------------------------------------------
def proof_monasheskaya_sosredotochennost(root=None):
    with workspace(root) as tmp:
        api, cid = campaign(tmp, "focus", "monk", 10, subclass="hand",
                            monsters=("ogre",), equipment=("dagger",))
        execute(api, cid, "start", kind="start_combat", encounter_id="yard")
        options = monks_focus_options()
        assert len(options) == 4, options
        reason = refusal(api, cid, "focus-none", kind="native_action",
                         operation="use_feature", feature_id="monks-focus")
        for option in options:
            assert option["activity_id"] in reason, (option, reason)
        reason = refusal(api, cid, "focus-bad", kind="native_action",
                         operation="use_feature", feature_id="monks-focus",
                         activity_id="not-an-activity")
        assert "варианта not-an-activity нет" in reason, reason
        assert spent(api, cid, "feature_use:monks-focus") is None

        patient = next(row for row in options if "Очко" in row["name"])
        result = execute(api, cid, "focus", kind="native_action", operation="use_feature",
                         feature_id="monks-focus", activity_id=patient["activity_id"])
        assert "effect_applied" in event_types(result), event_types(result)
        assert spent(api, cid, "feature_use:monks-focus") == 1
        # повтор в том же ходу: Бонусное действие потрачено, второе Очко не уходит
        refusal(api, cid, "focus-again", kind="native_action", operation="use_feature",
                feature_id="monks-focus", activity_id=patient["activity_id"])
        assert spent(api, cid, "feature_use:monks-focus") == 1


# --------------------------------------------------------------------------
# 8. Шквал ударов
# --------------------------------------------------------------------------
def proof_shkval_udarov(root=None):
    with workspace(root) as tmp:
        api, cid = campaign(tmp, "flurry", "monk", 10, subclass="hand",
                            monsters=("ogre",), equipment=("dagger",))
        execute(api, cid, "start", kind="start_combat", encounter_id="yard")
        result = execute(api, cid, "flurry", kind="native_action", operation="use_feature",
                         feature_id="monks-focus", activity_id=FLURRY)
        assert "intent_submitted" in event_types(result), event_types(result)
        assert hero_view(api, cid)["flurry_strikes_remaining"] == 3, hero_view(api, cid)
        assert spent(api, cid, "feature_use:monks-focus") == 1
        # оружием счётчик Шквала не читается — дыра была в прогоне r146, не в движке
        execute(api, cid, "armed", kind="native_action", operation="attack",
                weapon_id="dagger", target_id="foe0")
        assert hero_view(api, cid)["flurry_strikes_remaining"] == 3, hero_view(api, cid)
        strikes = 0
        for index in range(3):
            result = execute(api, cid, "strike%d" % index, kind="native_action",
                             operation="attack", weapon_id=UNARMED_STRIKE, target_id="foe0")
            assert "damage_applied" in event_types(result), (index, event_types(result))
            strikes += 1
            assert hero_view(api, cid)["flurry_strikes_remaining"] == 3 - strikes
        assert strikes == 3
        # повторный Шквал в том же ходу второго Очка не тратит
        reason = refusal(api, cid, "flurry-again", kind="native_action",
                         operation="use_feature", feature_id="monks-focus",
                         activity_id=FLURRY)
        assert "no_action_economy" in reason or "Бонусное" in reason or "bonus" in reason.lower(), reason
        assert spent(api, cid, "feature_use:monks-focus") == 1


R175_PROOFS = {
    "vardo_vdohnovenie": proof_vardo_vdohnovenie,
    "yarost": proof_yarost,
    "nepreryvnaya_yarost": proof_nepreryvnaya_yarost,
    "dikii_oblik": proof_dikii_oblik,
    "izgnanie_nezheti": proof_izgnanie_nezheti,
    "boevye_iskusstva": proof_boevye_iskusstva,
    "monasheskaya_sosredotochennost": proof_monasheskaya_sosredotochennost,
    "shkval_udarov": proof_shkval_udarov,
}

#: Обработчик в карте правил: реальный символ модуля, а не выдуманное имя.
#: `None` — хостового кода нет, всё делает движок (два «дыры нет»).
R175_HANDLERS = {
    "vardo_vdohnovenie": "nat20_content.feature_gate",
    "yarost": "nat20_content.feature_gate",
    "nepreryvnaya_yarost": None,
    "dikii_oblik": "nat20_content.feature_gate",
    "izgnanie_nezheti": "nat20_content.feature_gate",
    "boevye_iskusstva": "nat20_content.feature_gate",
    "monasheskaya_sosredotochennost": "nat20_content.feature_gate",
    "shkval_udarov": None,
}

#: Правила, которые остались «решает Мастер/хост» с названным блокером.
R175_BLOCKED = {"vardo_vdohnovenie": (
    "в бою кампании хоста один герой — получателя кости нет; "
    "путь хоста готов и проверен на проекции двух героев")}


@pytest.mark.parametrize("slug", sorted(R175_PROOFS))
def test_the_rule_reaches_the_game_through_the_host_path(slug, tmp_path):
    """Эффект, отказ с причиной и идемпотентность — по каждому из восьми умений."""
    R175_PROOFS[slug](tmp_path)


def test_screen_offers_the_class_features_the_host_accepts(tmp_path):
    api, cid = campaign(tmp_path, "screen-monk", "monk", 10, subclass="hand",
                        monsters=("ogre",), equipment=("dagger",))
    execute(api, cid, "start", kind="start_combat", encounter_id="yard")
    panel = Nat20Screen().project(api.store.campaign(cid))["commands"]
    labels = [command["label"] for command in panel]
    assert "Безоружный удар: Ogre" in labels, labels
    assert "Бонусный безоружный удар: Ogre" in labels, labels
    assert "Сосредоточенность: Шквал ударов" in labels, labels
    # кнопки Сосредоточенности несут activity_id, а не «нажми и посмотри»
    focus = [command for command in panel
             if command["label"].startswith("Сосредоточенность")]
    assert len(focus) == 4, focus
    assert all(row["parameters"].get("activity_id") for row in focus), focus

    api, cid = campaign(tmp_path, "screen-druid", "druid", 5, subclass="land",
                        monsters=("skeleton",), equipment=("dagger",))
    execute(api, cid, "start", kind="start_combat", encounter_id="yard")
    labels = [command["label"] for command in Nat20Screen().project(api.store.campaign(cid))["commands"]]
    # r207: на экране ровно известные формы (4/6/8), а не 48 зверей бестиария
    forms = [label for label in labels if label.startswith("Дикий облик: ")]
    assert len(forms) == 6, forms
    known = known_shapes(runtime(api, cid))
    assert [label for label in forms] == ["Дикий облик: %s (%s)" % (row["name"], row["id"])
                                          for row in known], (forms, known)


def test_the_eight_rules_are_recorded_as_executed_by_the_run():
    import json
    chapter = ROOT / "rules_data/v2/chapters/phb-03-classes"
    found = {}
    for path in sorted(chapter.glob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["rules"]:
            tail = row["id"].rsplit(".", 1)[-1]
            if tail in R175_PROOFS:
                found[tail] = row["readiness"]
    assert sorted(found) == sorted(R175_PROOFS), sorted(found)
    for tail, readiness in found.items():
        assert readiness["handler"] == R175_HANDLERS[tail], (tail, readiness["handler"])
        assert readiness["scope"].strip(), tail
        if tail in R175_BLOCKED:
            # Дыра хоста названа, а не спрятана за зелёным статусом.
            assert readiness["status"] == "adjudication_required", (tail, readiness["status"])
            assert readiness["blockers"] == [R175_BLOCKED[tail]], (tail, readiness["blockers"])
        else:
            assert readiness["status"] == "engine_subset_verified", (tail, readiness["status"])
            assert readiness["blockers"] == [], (tail, readiness["blockers"])


# --------------------------------------------------------------------------
# r207: известных форм 4/6/8, а не весь бестиарий под предел Опасности
# --------------------------------------------------------------------------
def _book_known_by_level():
    """`known=4|6|8` из записи правила «Дикий облик» (корпус, не память)."""
    import json as jsonlib
    chapter = jsonlib.loads((ROOT / "rules_data/v2/chapters/phb-03-classes/05-druid.json")
                            .read_text(encoding="utf-8"))
    rule = next(row for row in chapter["rules"] if row["id"].endswith("dikii_oblik"))
    return {int(level): int(cell.split("known=")[1].split("|")[0])
            for level, cell in rule["rule_parameters"]["shapes_by_level"].items()}


def test_known_shapes_follow_the_book(tmp_path):
    """Друид 2/4/5/8 знает 4/6/6/8 форм: число берётся из правила, а не из бестиария."""
    from solo.nat20_content import _KNOWN_SHAPES_BY_LEVEL

    book = _book_known_by_level()
    # таблица хоста не спорит с записью корпуса — иначе числа разойдутся молча
    assert dict(_KNOWN_SHAPES_BY_LEVEL) == book, (_KNOWN_SHAPES_BY_LEVEL, book)
    seen = {}
    for level in (2, 4, 5, 8):
        api, cid = campaign(tmp_path, "shapes-%d" % level, "druid", level,
                            subclass="land" if level >= 3 else None, monsters=("skeleton",))
        shapes = known_shapes(runtime(api, cid))
        expected = book[max(step for step in book if step <= level)]
        assert len(shapes) == expected, (level, shapes)
        seen[level] = [row["id"] for row in shapes]
    assert {level: len(ids) for level, ids in seen.items()} == {2: 4, 4: 6, 5: 6, 8: 8}, seen
    # новая ступень добавляет формы, а не подменяет уже известные
    assert set(seen[4]) <= set(seen[8]), (seen[4], seen[8])


def test_level_eight_known_shapes_include_a_flying_form(tmp_path):
    """С 8 уровня книга разрешает полёт — в известных формах есть летающий зверь.

    Без этого правило «летать с 8 уровня» недостижимо: набор хоста собирается
    по Опасности, а все летающие звери младше старших наземных.
    """
    from dnd5e_engine.activities.conjuration import wild_shape_tier
    from dnd5e_engine.lib_loader import get_lib_loader

    api, cid = campaign(tmp_path, "shapes-fly", "druid", 8, subclass="land",
                        monsters=("skeleton",))
    shapes = known_shapes(runtime(api, cid))
    assert len(shapes) == 8, shapes
    flying = [row for row in shapes if row["fly"]]
    assert len(flying) == 1, shapes
    monster = get_lib_loader().get_monster(flying[0]["id"])
    assert getattr(monster.movement, "fly", None), (flying[0], monster.movement)
    assert wild_shape_tier(8).fly_allowed
    # до 8 уровня полёт запрещён — и в наборе его быть не должно
    api, cid = campaign(tmp_path, "shapes-nofly", "druid", 4, subclass="land",
                        monsters=("skeleton",))
    assert not any(row["fly"] for row in known_shapes(runtime(api, cid)))


def test_known_shapes_are_the_table_forms_and_not_the_bestiary(tmp_path):
    """Список — известные формы: звери, проходящие таблицу, а не все звери под предел."""
    from dnd5e_engine.activities.conjuration import wild_shape_tier
    from dnd5e_engine.lib_loader import get_lib_loader

    api, cid = campaign(tmp_path, "shapes-table", "druid", 5, subclass="land",
                        monsters=("skeleton",))
    shapes = known_shapes(runtime(api, cid))
    tier = wild_shape_tier(5)
    loader = get_lib_loader()
    for row in shapes:
        monster = loader.get_monster(row["id"])
        assert monster is not None and str(monster.creature_type) == "beast", row
        assert float(monster.cr) <= tier.max_cr, (row, tier)
        assert not (getattr(monster.movement, "fly", None) and not tier.fly_allowed), row
    eligible = [slug for slug in loader.list_slugs("monsters")
                if (monster := loader.get_monster(slug)) is not None
                and str(monster.creature_type) == "beast"
                and getattr(monster, "cr", None) is not None
                and float(monster.cr) <= tier.max_cr
                and not (getattr(monster.movement, "fly", None) and not tier.fly_allowed)
                and getattr(monster, "ac", None) is not None]
    # бестиарий под предел Опасности шире известных форм — это и был дефект
    assert len(eligible) > len(shapes), (len(eligible), len(shapes))
    # список — копия: правка строки игроком не портит кэш движка
    shapes[0]["name"] = "Mutated"
    assert known_shapes(runtime(api, cid))[0]["name"] != "Mutated"
    assert not set(eligible) <= {row["id"] for row in shapes}, shapes
