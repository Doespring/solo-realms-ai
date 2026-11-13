"""Pinned Nat20 runtime + minimal typed Foundry 2024 corpus. No fallback packs."""
import hashlib
from functools import lru_cache
from importlib.metadata import version
import json
import logging
import re
from pathlib import Path
from .content_verification_cache import verify_tree, read_json

from dnd5e_srd_data.loader import MemoryAssetLoader
from dnd5e_srd_data.schema.class_ import Class
from dnd5e_srd_data.schema.feature import Feature
from dnd5e_srd_data.schema.item import Armor, Weapon
from dnd5e_srd_data.schema.monster import Monster
from dnd5e_srd_data.schema.species import Species

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "integration/nat20/imported"
VENDOR = ROOT / "vendor/nat20"

#: r196: журнал сборки листа. Одна строка на лист со счётчиками, а не по
#: строке stderr на каждое потерянное умение (до r196 их было 135 на прогон
#: по 48 подклассам, и они не говорили, что именно потеряно).
SHEET_LOG = logging.getLogger("solo.nat20_content.sheet")

# One trusted registry for both the worker and the master's available check IDs.
# (kind, skill, ability, dc). Every id the engine publishes in 'available_checks'
# must be reachable from chat, otherwise the table has a sheet with eighteen
# skills and only three of them can be used. The DC is the standard difficulty
# for a routine attempt at the named task; a scene may override it through
# definition.checks.
_SKILLS = {"acrobatics": "dex", "animal_handling": "wis", "arcana": "int", "athletics": "str",
           "deception": "cha", "history": "int", "insight": "wis", "intimidation": "cha",
           "investigation": "int", "medicine": "wis", "nature": "int", "perception": "wis",
           "performance": "cha", "persuasion": "cha", "religion": "int", "sleight_of_hand": "dex",
           "stealth": "dex", "survival": "wis"}
_ABILITIES = {"str", "dex", "con", "int", "wis", "cha"}
CHECKS = {name: ("skill", name, None, 15) for name in _SKILLS}
CHECKS.update({name: ("ability", None, name, 15) for name in _ABILITIES})
CHECKS.update({"save-" + name: ("saving_throw", None, name.upper(), 15) for name in _ABILITIES})
# Perception is called for constantly at the table, so its default is lower.
CHECKS["perception"] = ("skill", "perception", None, 12)

# PHB 2024 chapter 1 "Действия": Влияние, Изучение and Поиск outside combat.
# Each of the three is one ability check, and the engine already resolves checks
# (nat20_worker -> nat20_checks.resolve_state_check -> dnd5e_engine.resolve_check).
# So this table is data only: it names the action and the skills that lead to it,
# and gives every (action, skill) pair its own check id. The skill lists follow
# the reviewed scope of this iteration; the corpus entries for these actions name
# skills but no DC, so the DC a scene action rolls is the host registry default
# ("registry") unless the scene's own definition.checks entry carries one
# ("scene"). Nothing here may be reported as a value printed in the book.
SCENE_ACTIONS = {
    # Список навыков Влияния — из книги (стр. 15): Харизма (Выступление,
    # Запугивание, Обман, Убеждение) или Мудрость (Уход за животными).
    # До r179 здесь стояли три навыка Харизмы, и «Выступление» с «Уходом за
    # животными» до игры не доходили, хотя книга их печатает.
    "influence": dict(name="Влиять", noun="Влияние",
                      skills={"persuasion": "influence-persuasion",
                              "deception": "influence-deception",
                              "intimidation": "influence-intimidation",
                              "performance": "influence-performance",
                              "animal_handling": "influence-animal-handling"}),
    "study": dict(name="Изучить", noun="Изучение",
                  skills={"insight": "study-insight", "arcana": "study-arcana",
                          "history": "study-history", "religion": "study-religion",
                          "nature": "study-nature", "investigation": "study-investigation"}),
    "search": dict(name="Искать", noun="Поиск",
                   skills={"perception": "search-perception",
                           "investigation": "search-investigation"}),
}
SKILL_NAMES = {"perception": "Восприятие", "investigation": "Расследование",
               "insight": "Проницательность", "persuasion": "Убеждение",
               "deception": "Обман", "intimidation": "Запугивание",
               "performance": "Выступление", "animal_handling": "Уход за животными",
               "arcana": "Магия", "history": "История", "religion": "Религия",
               "nature": "Природа"}
# One registry row per (action, skill): the same skill keeps the same default DC
# it has as a plain check, so the table cannot drift into two numbers per skill.
SCENE_CHECKS = {check_id: CHECKS[skill] for spec in SCENE_ACTIONS.values()
                for skill, check_id in spec["skills"].items()}


def scene_check(action_id, skill, definition=None):
    """Resolve one scene action into the check the engine must roll.

    DC order: the scene's own ``definition.checks`` entry, then this registry.
    The function reports which of the two answered, so the journal states the
    source instead of implying a book value. An entry that is not a plain skill
    check cannot stand in for a scene action and falls back to the registry.
    """
    check_id = SCENE_ACTIONS[action_id]["skills"][skill]
    entry = ((getattr(definition, "checks", None) or {}).get(check_id)
             if definition is not None else None)
    if entry is not None and entry.kind == "skill" and entry.dc is not None:
        return dict(kind=entry.kind, skill=entry.skill or skill, ability=entry.ability,
                    dc=entry.dc, check_id=check_id, source="scene",
                    advantage=bool(getattr(entry, "advantage", False)),
                    disadvantage=bool(getattr(entry, "disadvantage", False)))
    kind, skill_name, ability, dc = SCENE_CHECKS[check_id]
    return dict(kind=kind, skill=skill_name, ability=ability, dc=dc, check_id=check_id,
                source="registry", advantage=False, disadvantage=False)


# r162: маркер источника у каждой записи. Решение владельца 01.11 — на все id
# ставится маркер `srd`/`phb`, включая существующие: одна выборка по маркеру
# делит корпус на SRD-часть (CC-BY-4.0, можно публиковать) и PHB-часть
# (купленная книга владельца, только для личного стола). В корпусе маркер стоит
# в самом id (`phb2024.*`/`srd2024.*`) и в `source.kind`; записи движка файлов
# не меняют — их метит этот хост-слой, потому что вендор и `PATCHES.json` под
# пином и правке не подлежат.
SOURCE_KINDS = ("phb", "srd")
KIND_PREFIX = {"phb": "phb2024.", "srd": "srd2024."}
KIND_PROVENANCE = {"phb": "phb-2024-ru", "srd": "srd-5.2.1"}
KIND_LICENSE = {"phb": "user_private", "srd": "CC-BY-4.0"}
PHB_DOCUMENT = "phb_2024_ru"


def corpus_marker(record):
    """Маркер записи корпуса: id и `source.kind` обязаны говорить одно и то же.

    Возвращает `dict(id, kind, provenance, license)`. Запись без маркера,
    с незнакомым `kind` или с id, чей префикс спорит с `source.kind`, — это не
    «неразмеченная запись», а дефект данных: функция бросает `ValueError`, и
    страж-тест валит прогон.
    """
    rule_id = record.get("id")
    source = record.get("source") or {}
    kind = source.get("kind")
    if kind not in SOURCE_KINDS:
        raise ValueError(f"record without source.kind marker: {rule_id}")
    if not isinstance(rule_id, str) or not rule_id.startswith(KIND_PREFIX[kind]):
        raise ValueError(f"record id does not carry the {kind} marker: {rule_id}")
    if kind == "phb" and source.get("document_id") != PHB_DOCUMENT:
        raise ValueError(f"phb record without the owner's book: {rule_id}")
    return dict(id=rule_id, kind=kind, provenance=KIND_PROVENANCE[kind],
                license=KIND_LICENSE[kind])


def engine_marker(slug, provenance=None):
    """Маркер записи движка: пакет SRD 5.2.1 или наши файлы PHB 2024.

    Пакетная запись получает `srd`/`srd-5.2.1` (её лицензия задана манифестом
    релиза, а не полем записи), запись из каталога владельца — `phb`/
    `phb-2024-ru`. Признак PHB берётся из самой записи (`provenance.source` от
    r147 или маркер в slug), а не из вызывающего кода: иначе каталог и лист
    персонажа могли бы разметить один и тот же слаг по-разному.
    """
    prov = provenance if isinstance(provenance, dict) else {}
    ours = prov.get("source") == KIND_PROVENANCE["phb"] or str(slug or "").startswith(KIND_PREFIX["phb"])
    kind = "phb" if ours else "srd"
    marker = dict(kind=kind, provenance=KIND_PROVENANCE[kind], license=KIND_LICENSE[kind])
    pages = prov.get("printed_pages") or prov.get("printed_page")
    if kind == "phb" and pages:
        marker["printed_pages"] = pages
    return marker


def weapons():
    """Every weapon the D&D 2024 dataset knows, as the engine names it.

    The chat used to offer four hard-coded weapons (club, longsword, shortbow,
    unarmed) while the engine resolved attacks against all 86. The table could
    not say 'I draw a dagger' because nothing told it the word existed.
    """
    from dnd5e_srd_data.loader import BundledAssetLoader
    loader = BundledAssetLoader()
    found = {}
    for slug in loader.list_slugs('items'):
        try:
            item = loader.get_weapon(slug)
        except Exception:
            continue  # not a weapon: armour, gear, magic item
        if item is None:
            continue
        found[slug] = dict(name=item.name, category=str(item.weapon_category),
                           damage=[(part.dice, part.damage_type) for part in item.damage_parts],
                           mastery=str(item.mastery) if item.mastery else '',
                           weight=item.weight)
    return found


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


# One reviewed patch on top of the pinned commit. Every file it touches is listed
# here with the SHA of the reviewed content, so the pin stays an exact inventory:
# an edit that is not in this table is still an unreviewed revision. UPSTREAM.json
# keeps the upstream SHA of the patched files; the reviewed SHAs live beside it in
# vendor/nat20/PATCHES.json and both sides are checked.
PATCHES = VENDOR / 'PATCHES.json'


def _reviewed(lock):
    """The pin's file map with every reviewed patch applied to it."""
    if not PATCHES.is_file():
        return dict(lock["files"])
    patches = read_json(PATCHES)
    if patches.get("commit") != lock["commit"]:
        raise ValueError("Reviewed Nat20 patch was made against another upstream commit")
    files = dict(lock["files"])
    for entry in patches.get("files") or ():
        name, sha, kind = entry.get("path"), entry.get("sha256"), entry.get("kind")
        inside = name in files if kind == "modified" else kind == "added"
        if (not inside or not isinstance(name, str) or not name.endswith(".py")
                or not isinstance(sha, str) or len(sha) != 64
                or any(c not in "0123456789abcdef" for c in sha)):
            raise ValueError("Reviewed Nat20 patch names a file outside the pin")
        files[name] = sha
    return files


def engine_identity():
    """Which rules produced a combat save's numbers.

    ``release()``'s fingerprint deliberately names the *upstream* pin only: the
    comment there explains that every historical release digest must keep
    resolving to the same bytes, and that a reviewed patch "adds a module and
    changes no existing code path". r177/r181 broke that assumption - they edit
    existing engine code, so the pin stays the same while the engine answers a
    different snapshot for the same recipe. A durable fight bound only to the
    pin therefore cannot tell "the engine was patched" from "the save is
    corrupt", and refuses both with the same hard error.

    This fingerprint covers the reviewed inventory (pin plus every PATCHES.json
    entry), which is exactly the set of rules the installed tree was verified
    against a moment ago. It is cheap - two JSON reads, no tree hashing.
    """
    lock = read_json(VENDOR / 'UPSTREAM.json')
    reviewed = _reviewed(lock)
    patches = read_json(PATCHES) if PATCHES.is_file() else {}
    return {"release": str(patches.get("version") or "upstream"),
            "upstream": lock["commit"], "rules": digest(reviewed)}


def release(selector=None):
    import dnd5e_engine
    import dnd5e_srd_data
    lock = read_json(VENDOR / 'UPSTREAM.json')
    if lock["commit"] != "e187c813bb4e43c4e4e62942ecd83831f1cdbcaa":
        raise ValueError("Unreviewed Nat20 upstream revision")
    reviewed = _reviewed(lock)
    installed = {"dnd5e-engine": Path(dnd5e_engine.__file__).parent,
                 "dnd5e-srd-data": Path(dnd5e_srd_data.__file__).parent}
    runtime_hashes = {}
    for package, package_root in installed.items():
        if version(package) != "0.7.0":
            raise ValueError("Nat20 package version mismatch")
        prefix = f"packages/{package}/src/{package.replace('-', '_')}/"
        pinned = {name.removeprefix(prefix): sha for name, sha in lock["files"].items()
                  if name.startswith(prefix) and name.endswith(".py")}
        expected = {name.removeprefix(prefix): sha for name, sha in reviewed.items()
                    if name.startswith(prefix) and name.endswith(".py")}
        # The installed tree must match the pin plus every reviewed patch, file
        # for file; an unlisted edit is still an unreviewed revision.
        verify_tree(package_root, expected, suffix='.py')
        # The fingerprint keeps identifying the *upstream* pin, so every
        # historical release digest still resolves to these same bytes. The
        # reviewed patch is identified by PATCHES.json instead: it adds a
        # module and changes no existing code path, so replay is identical.
        runtime_hashes[package] = digest(pinned)
    if version("d20") != "1.1.2" or version("lark-parser") != "0.9.0":
        raise ValueError("Nat20 dice dependency changed")
    manifest = read_json(CONTENT / 'manifest.json')
    if (manifest["rules_version"], manifest["foundry_version"], manifest["nat20_version"]) != ("dnd2024", "5.3.3", "0.7.0"):
        raise ValueError("Content rules edition/release mismatch")
    documents = {"classes": [], "species": [], "features": [], "items": [], "monsters": []}
    models = {"classes": Class, "species": Species, "features": Feature, "monsters": Monster}
    identities = set()
    for record in manifest["documents"]:
        path = (CONTENT / record["file"]).resolve()
        if not path.is_relative_to(CONTENT.resolve()) or record["rules_version"] != "dnd2024" or record["license"] != "CC-BY-4.0":
            raise ValueError("Unreviewed content provenance")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("Imported content changed")
        category = record["category"]
        body = json.loads(raw)
        if body["slug"] != record["slug"] or (category, body["slug"]) in identities:
            raise ValueError("Content identity mismatch")
        identities.add((category, body["slug"]))
        model = (Weapon if body.get("slug") == "longsword" else Armor) if category == "items" else models[category]
        documents[category].append(model.model_validate(body))
    profile_record = manifest["encounter_profile"]
    profile_path = (CONTENT / profile_record["file"]).resolve()
    if not profile_path.is_relative_to(CONTENT.resolve()) or hashlib.sha256(profile_path.read_bytes()).hexdigest() != profile_record["sha256"]:
        raise ValueError("Encounter profile changed")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if profile["rules_version"] != "dnd2024" or profile["monster"] != "goblin-warrior":
        raise ValueError("Encounter profile edition mismatch")
    monster = documents["monsters"][0].model_dump(mode="json")
    action = next(row for row in monster["actions"] if row["slug"] == profile["action"])
    activity = next(row for row in action["activities"] if row["id"] == profile["activity_id"])
    if len(activity["damage"]["parts"]) != 1 or activity["damage"]["parts"][0]["bonus"]:
        raise ValueError("Unreviewed encounter damage shape")
    activity["attack"]["ability"] = profile["governing_ability"]
    # The ability modifier is NOT written into the damage formula here. The
    # engine adds it itself: ``_implicit_mod_damage_parts`` appends ``@mod`` to
    # the first part of ``include_base`` stat-block attack, reading the
    # monster's own DEX through ``stat_block_magnitudes``. Writing the same +2
    # into ``parts[0].bonus`` made the goblin's Scimitar 1d6+4 (this test stood
    # at 6 damage on a d6=2 fixture) — the modifier counted twice. The profile
    # still states ``damage_ability_bonus``; it is a claim about the projection,
    # verified by ``tests/test_nat20_monster_projection.py`` against the engine's
    # own ``@mod``, not an input to it.
    action["activities"] = [activity]
    monster["actions"] = [action]
    documents["monsters"] = [Monster.model_validate(monster)]
    fingerprint = digest({"runtime": runtime_hashes, "content": manifest,
                          "dice_versions": {name: version(name) for name in ("d20", "lark-parser", "cachetools")}})
    # Omitted selector and every historical fighter digest retain the exact
    # Preview7 corpus/fingerprint. An extension never mutates that manifest.
    if selector not in (None, 'fighter-2024-v1', fingerprint):
        from .nat20_prototype import RELEASE as PROTOTYPE, MANIFEST, bundled_release
        if selector == PROTOTYPE or (MANIFEST.is_file() and selector == digest({'runtime_parent':fingerprint,'bundled_data':json.loads(MANIFEST.read_text(encoding='utf-8'))})):
            return bundled_release(fingerprint, selector)
        from .nat20_bless import RELEASE, ROOT as EXTENSION_ROOT, extension, require_stand
        if selector != RELEASE:
            path = EXTENSION_ROOT / 'manifest.json'
            known = digest({'parent_release_digest': fingerprint,
                            'extension': json.loads(path.read_text(encoding='utf-8'))}) if path.is_file() else None
            if selector != known:
                raise ValueError('Pinned engine/content release changed; unknown release, explicit migration required')
        require_stand()
        documents, extended_fingerprint = extension(documents, fingerprint)
        if selector not in (RELEASE, extended_fingerprint):
            raise ValueError('Pinned engine/content release changed; unknown release, explicit migration required')
        fingerprint = extended_fingerprint
    loader = MemoryAssetLoader(**documents)
    return loader, fingerprint, profile


# ---------------------------------------------------------------------------
# Что делает умение, когда игрок его нажимает (r170)
#
# Лист персонажа перечисляет умения подклассов, но нажатие `use_feature` не
# всегда меняет состояние: у части умений в пине есть только дословный текст,
# у части активность-утилита без `effects`, у части формула лечения со
# `@scaling`, которую движок для умений не подставляет. Игрок при этом видел
# либо «ничего», либо внутреннюю ошибку движка.
#
# Этот раздел — один честный ответ на вопрос «что будет, если нажать»: он
# читает запись умения из пина (или из записей книги) и говорит одно из
# четырёх. Ни одна строка здесь не выдумывает механику: решение принимается по
# полям самой записи, а прогон `tests/test_effect_proof_subclass_features.py`
# проверяет, что обещание плана совпадает с поведением движка.
#
# * `native` — у умения одна типизированная активность, которая меняет
#   состояние (урон, лечение, спасбросок, проверка); хост обязан передать её
#   `activity_id` и, если активность бьёт по цели, `target_id`;
# * `choice` — активностей несколько, это выбор игрока (Крит. удар: «Первое»/
#   «Повторное»), хост обязан спросить вариант, а не угадывать;
# * `master` — типизированной активности нет: умение ведёт Мастер;
# * `blocked` — активность есть, но движок пина её не разрешает (утилита без
#   effects, формула `@scaling`, пассивный ключ, который никто не читает);
#   причина названа, правка нужна в вендоре.
FEATURE_PLAN_STATUSES = ("native", "choice", "master", "blocked")

#: Умения, у которых пассивное изменение замерено как невидимое (r170): лист
#: берсерка 6 без `condition_immunities`, чемпион без порога крита, вор без
#: скорости лазания и предела настройки. Список по СЛАГАМ, а не по ключам:
#: тот же ключ у другого умения может проецироваться (`system.traits.ci.value`
#: у «Оберега природы» реально кладёт иммунитет в бой), поэтому обещать «движок
#: не читает ключ» вообще нельзя — можно только «у этого умения не читает».
_MEASURED_UNREACHED_PASSIVES = {
    "mindless-rage": ("system.traits.ci.value",
                      "иммунитеты к состояниям не появляются ни в листе, ни в бою "
                      "(замер: лист берсерка 6 без condition_immunities)"),
    "improved-critical": ("flags.dnd5e.weaponCriticalThreshold",
                          "порог критического попадания движок не читает"),
    "superior-critical": ("flags.dnd5e.weaponCriticalThreshold",
                          "порог критического попадания движок не читает"),
    "remarkable-athlete": ("flags.dnd5e.remarkableAthlete",
                           "признак Remarkable Athlete движок не читает"),
    "second-story-work": ("system.attributes.movement.climb",
                          "скорость лазания не появляется в листе (замер: base_speed без climb)"),
    "use-magic-device": ("system.attributes.attunement.max",
                         "предел настройки не появляется в листе персонажа (замер)"),
}


def _activity_dice_free(activity) -> bool:
    """Урон-часть без костей и без добавки: `denomination=0`, числа нет, бонуса нет.

    Движок пишет по такой части `damage_part_untyped` и не наносит урона —
    это и есть наблюдаемое поведение «нажал, ничего не произошло». Часть с
    одной добавкой (`@abilities.int.mod` у Empowered Evocation) урон даёт:
    прогон r170 показал 1 пункт без костей.
    """
    parts = list(getattr(getattr(activity, "damage", None), "parts", []) or [])
    if not parts:
        return True
    for part in parts:
        number = getattr(part, "number", None)
        denomination = getattr(part, "denomination", 0) or 0
        custom = getattr(getattr(part, "custom", None), "formula", "") or ""
        bonus = getattr(part, "bonus", "") or ""
        scaling = getattr(getattr(part, "scaling", None), "formula", "") or ""
        if (denomination > 0 and number) or custom.strip() or bonus.strip() or scaling.strip():
            return False
    return True


def _formula_tokens(activity) -> list[str]:
    """Формулы лечения активности: bonus, custom.formula, scaling.formula."""
    healing = getattr(activity, "healing", None)
    if healing is None:
        return []
    return [healing.bonus or "", getattr(healing.custom, "formula", "") or "",
            getattr(healing.scaling, "formula", "") or ""]


def _activity_plan(activity, feature=None) -> dict:
    kind = str(getattr(activity, "kind", "") or "")
    plan = dict(activity_id=str(activity.id), name=str(getattr(activity, "name", "") or ""),
                kind=kind, status="native", needs_target=False, reason="")
    if kind == "damage":
        plan["needs_target"] = True
        if _activity_dice_free(activity):
            plan.update(status="blocked", needs_target=False, reason=(
                "урон-часть активности без костей (denomination=0, числа нет): движок "
                "пишет damage_part_untyped и не наносит урона"))
        return plan
    if kind == "heal":
        tokens = _formula_tokens(activity)
        if any("@scaling" in token for token in tokens):
            plan.update(status="blocked", reason=(
                "формула лечения ссылается на @scaling, а для умений движок его не "
                "подставляет: вызов падает ValueError: Unhandled roll-data token: '@scaling'"))
        elif any("(" in token for token in tokens):
            # «Тьма в подарок» (fiend): `max(1, @abilities.cha.mod + ...)`.
            # Движок разбирает только кости и токены, вызов функции — нет:
            # ValueError: Unparseable dice expression (замер критика r170).
            plan.update(status="blocked", reason=(
                "в формуле лечения вызов функции (max(...)): движок разбирает только "
                "кости и @-токены и падает ValueError: Unparseable dice expression"))
        return plan
    if kind == "save":
        # Спасбросок умения часто бьёт одну названную цель, иногда — площадку.
        # Цель не требуем (у площадных её нет), но и не выкидываем: движок без
        # неё отвечает только IntentSubmitted (замер: hurl-through-hell).
        return plan
    if kind == "check":
        return plan
    if kind == "utility":
        if any(getattr(effect, "changes", None) or getattr(effect, "statuses", None)
               for effect in getattr(activity, "effects", None) or []):
            return plan
        if getattr(activity, "applied_effects", None):
            return plan
        if _regains_own_uses(activity):
            # «Вернуть применение» (holy-nimbus, hurl-through-hell): активность
            # возвращает своё применение, но счётчик не меняется — замер r170.
            plan.update(status="blocked", reason=(
                "активность возвращает своё применение, но счётчик применений движок "
                "не меняет: событие только IntentSubmitted"))
            return plan
        foreign = _foreign_pool_spend(activity)
        if foreign and _own_uses(feature):
            # «Восстановить ярость» (Безжалостная ярость): активность метит в
            # чужой запас (ярость) и возвращает своё применение; замер r170 —
            # движок вместо восстановления пишет `feature_use:rage`.
            plan.update(status="blocked", reason=(
                f"активность тратит чужой запас ({foreign}) вместо восстановления своего: "
                "движок увеличивает применение того умения и умение не работает"))
            return plan
        if _own_uses(feature):
            # Замер: у умения со своим запасом применений (Всплеск действий,
            # Непоколебимость, Восстановление магии) нажатие тратит применение —
            # это наблюдаемое изменение (`feature_use:<slug>`), даже когда
            # активность-утилита не несёт effects.
            plan["effect"] = "feature_use"
            return plan
        plan.update(status="blocked", reason=(
            "активность-утилита без effects и без своего запаса применений: движок "
            "принимает умение и не меняет состояние"))
        return plan
    if kind == "transform":
        # Превращение движок исполняет (Wild Shape, проверено r146).
        return plan
    plan.update(status="blocked", reason=(
        f"активность вида «{kind}» движок пина не разрешает: use_feature её не исполняет"))
    return plan


def _own_uses(feature) -> bool:
    """Есть ли у умения свой запас применений (`uses.max`)."""
    uses = getattr(feature, "uses", None) if feature is not None else None
    return bool(getattr(uses, "max", None))


def _foreign_pool_spend(activity) -> str | None:
    """Пул чужого умения, в который активность пишет положительное число.

    Без этого «Восстановить ярость» выглядело как рабочая утилита: у умения
    есть свой запас, но активность метит в запас другого умения и увеличивает
    именно его счётчик.
    """
    for target in getattr(getattr(activity, "consumption", None), "targets", None) or []:
        if str(getattr(target, "type", "")) != "itemUses":
            continue
        try:
            amount = int(str(getattr(target, "value", "")).strip())
        except ValueError:
            continue
        pool = str(getattr(target, "target", "") or "").strip()
        if amount > 0 and pool:
            return pool
    return None


def _regains_own_uses(activity) -> bool:
    """Активность возвращает своё применение (отрицательное число в своём пуле)."""
    for target in getattr(getattr(activity, "consumption", None), "targets", None) or []:
        if str(getattr(target, "type", "")) != "itemUses":
            continue
        if str(getattr(target, "target", "") or "").strip():
            continue
        try:
            if int(str(getattr(target, "value", "")).strip()) < 0:
                return True
        except ValueError:
            continue
    return False


def _feature_passive_keys(feature) -> list[str]:
    keys = []
    for effect in getattr(feature, "passive_effects", None) or []:
        for change in getattr(effect, "changes", None) or []:
            key = getattr(change, "key", None)
            if key:
                keys.append(str(key))
    return keys


def _book_feature(slug):
    """Умение подкласса из записей книги (r147), если пина оно не знает."""
    from . import nat20_canonical
    found = nat20_canonical.feature_text(slug)
    return None if found is None else found[0]


def _plan_for(slug: str) -> dict | None:
    from dnd5e_engine.lib_loader import get_lib_loader
    from .nat20_canonical import PhbFeature
    feature = get_lib_loader().get_feature(slug)
    text = _book_feature(slug)
    # r196: загрузчик теперь отдаёт умение книги как `Feature` (запись есть,
    # механики нет). План такого умения остаётся книжным со страницей: иначе
    # вместо точной причины игрок получил бы общее «активности в пине нет».
    if text is not None and (feature is None or isinstance(feature, PhbFeature)):
        pages = ", ".join(str(page) for page in text.printed_pages)
        return dict(slug=slug, name=text.name, source="phb-2024-ru", activities=[],
                    status="master", needs_target=False, activity_id=None,
                    reason=(f"умение из книги, эффект не автоматизирован: «{text.name}» "
                            f"перенесено дословно (PHB 2024, стр. {pages}), типизированной "
                            "активности нет — движок его не исполняет, механику ведёт Мастер"))
    if feature is None:
        return None
    activities = [_activity_plan(activity, feature)
                  for activity in getattr(feature, "activities", []) or []]
    base = dict(slug=slug, name=str(getattr(feature, "name", "") or slug), source="pin",
                activities=activities, needs_target=False, activity_id=None)
    if len(activities) > 1:
        return dict(base, status="choice", reason=(
            "активностей несколько: движок ждёт выбранный activity_id и без него "
            "отвечает CastFailed(activity_not_selected)"))
    if activities:
        alone = activities[0]
        return dict(base, status=alone["status"], reason=alone["reason"],
                    needs_target=alone["needs_target"], activity_id=alone["activity_id"],
                    kind=alone["kind"])
    passive = _feature_passive_keys(feature)
    measured = _MEASURED_UNREACHED_PASSIVES.get(str(slug))
    if measured and (not passive or measured[0] in passive):
        return dict(base, status="blocked", reason=(
            f"умение держится на пассивном изменении {measured[0]}: {measured[1]}; "
            "use_feature отвечает feature_not_executable"))
    if passive:
        return dict(base, status="master", reason=(
            f"пассивное умение (ключи {', '.join(passive)}) без типизированной активности: "
            "use_feature отвечает feature_not_executable, механику ведёт Мастер"))
    return dict(base, status="master", reason=(
        "типизированной активности в пине нет: use_feature отвечает "
        "feature_not_executable, механику ведёт Мастер"))


def feature_plan(slug: str) -> dict | None:
    """Честный план одного умения: `native`/`choice`/`master`/`blocked`.

    `None` — такого умения нет ни в пине, ни в записях книги. План читается
    только из данных; чтобы обещание не разошлось с движком, его проверяет
    прогон (`tests/test_effect_proof_subclass_features.py`). Копия отдаётся
    наружу каждый раз: вызывающий не должен уметь отравить следующий ответ,
    правя `status` в возвращённом словаре.
    """
    plan = _plan_for(str(slug))
    return None if plan is None else dict(plan, activities=[dict(row) for row in plan["activities"]])


# ---------------------------------------------------------------------------
# r196: журнал сборки листа — счётчик вместо строки stderr на каждое умение
#
# До r196 `_always_on_changes` писал в stderr по строке на каждый слаг, которого
# нет в пине, и шёл дальше: 135 строк на прогон по 48 подклассам, ни числа, ни
# имени потерянного умения в листе. Теперь у каждого выданного умения есть
# разбор: `pin` (типизированная запись пина), `book` (умение книги: текст и
# страница есть, механики нет — `adjudication_required`) либо `missing` (нет ни
# записи пина, ни книги; это дефект данных, и он попадает в журнал предупреждением).
# ---------------------------------------------------------------------------
GRANTED_STATUSES = {
    "pin": "умение есть в пине: действует то, что в нём записано",
    "book": "умение книги: текст и страница есть, эффект не автоматизирован",
    "missing": "нет ни записи пина, ни текста книги: дефект данных",
}


def granted_feature_journal(features, *, loader=None) -> dict:
    """Разбор выданных герою умений: сколько разобралось и чем именно.

    Читает только данные: пин через загрузчик, книгу через `nat20_canonical`.
    Возвращает счётчики и строки — лист показывает их рядом с умением, а
    `derive_sheet_with_journal` пишет одну строку в `SHEET_LOG`. Повторные
    слаги считаются один раз: лист перечисляет умение однажды.
    """
    from dnd5e_engine.lib_loader import get_lib_loader
    from .nat20_canonical import PhbFeature, feature_text
    loader = loader or get_lib_loader()
    rows = {"pin": [], "book": [], "missing": []}
    seen = set()
    for raw in features:
        slug = str(raw)
        if slug in seen:
            continue
        seen.add(slug)
        feature = loader.get_feature(slug)
        text = feature_text(slug)
        if text is not None and (feature is None or isinstance(feature, PhbFeature)):
            printed, record = text
            rows["book"].append(dict(
                id=slug, name=printed.name, status="adjudication_required",
                level=printed.level, printed_pages=list(printed.printed_pages),
                subclass=record.slug, source=record.marker(),
                reason=(f"умение из книги, эффект не автоматизирован: "
                        f"{printed.blocker}"),
            ))
        elif feature is not None:
            rows["pin"].append(dict(id=slug, name=str(feature.name or slug),
                                    status="pin"))
        else:
            rows["missing"].append(dict(
                id=slug, name=slug, status="missing",
                reason=("выдано записью класса/подкласса, но записи умения нет ни в "
                        "пине, ни в перенесённых текстах книги: движок его пропускает")))
    journal = {
        "scanned": len(seen),
        "pin": len(rows["pin"]),
        "book": len(rows["book"]),
        "missing": len(rows["missing"]),
        "rows": rows,
        "statuses": dict(GRANTED_STATUSES),
    }
    return journal


def granted_feature_warnings(journal: dict) -> list[str]:
    """Человеческие строки для экрана сборки: что из книги показано текстом."""
    lines = []
    book = journal.get("rows", {}).get("book") or []
    if book:
        pages = sorted({page for row in book for page in row.get("printed_pages") or []})
        where = f"; напечатанные страницы: {', '.join(str(p) for p in pages)}" if pages else ""
        lines.append(
            f"В листе {len(book)} умений из книги: текст перенесён дословно, эффект не "
            f"автоматизирован — механику ведёт Мастер{where}.")
    missing = journal.get("rows", {}).get("missing") or []
    if missing:
        names = ", ".join(row["id"] for row in missing[:5])
        more = "" if len(missing) <= 5 else f" и ещё {len(missing) - 5}"
        lines.append(f"Нет записи для {len(missing)} выданных умений: {names}{more}.")
    return lines


def derive_sheet_with_journal(spec, *, loader=None):
    """`derive_sheet` + журнал умений: (лист, журнал).

    Хост-сборка листа идёт через эту обёртку, чтобы счётчик пропусков был
    наблюдаемым (одна строка `SHEET_LOG` на лист), а экран мог показать
    «умение из книги, эффект не автоматизирован» вместо тишины.
    """
    from dnd5e_engine.build_spec import derive_sheet
    from dnd5e_engine.lib_loader import get_lib_loader
    loader = loader or get_lib_loader()
    sheet = derive_sheet(spec, loader=loader)
    journal = granted_feature_journal(sheet.features, loader=loader)
    SHEET_LOG.info(
        "sheet build: granted=%d pin=%d book=%d missing=%d",
        journal["scanned"], journal["pin"], journal["book"], journal["missing"])
    if journal["missing"]:
        SHEET_LOG.warning(
            "granted features without any record: %s",
            ", ".join(row["id"] for row in journal["rows"]["missing"]))
    return sheet, journal


def subclass_feature_slugs() -> frozenset:
    """Слаги всех умений подклассов, которые движок вообще знает.

    Хост водит через `feature_intent` только их: базовые умения классов уже
    проходят свой путь (`known_issue` + отказ «нет механического эффекта»), и
    менять его этой карточкой нельзя. Считается каждый раз: `configure_lib_loader`
    подменяет библиотеку, и запомненный ответ пережил бы подмену.
    """
    from dnd5e_engine.lib_loader import get_lib_loader
    loader = get_lib_loader()
    found = set()
    for subclass_slug in loader.list_slugs("subclasses"):
        record = loader.get_subclass(subclass_slug)
        for granted in getattr(record, "granted_features", None) or []:
            ref_type = getattr(granted, "ref_type", "feature")
            ref_type = getattr(ref_type, "value", ref_type)
            if str(ref_type) == "feature" and getattr(granted, "slug", None):
                found.add(str(granted.slug))
    return frozenset(found)


def is_subclass_feature(slug: str) -> bool:
    """Умение подкласса (в том числе запись книги) или базовое умение класса."""
    if str(slug).startswith(KIND_PREFIX["phb"]):
        return True
    return str(slug) in subclass_feature_slugs()


def feature_intent(slug: str, activity_id: str | None = None,
                   target_id: str | None = None) -> dict:
    """Значения интента `use_feature`, при которых нажатие что-то меняет.

    Хост не угадывает за игрока: без выбранного варианта у умения с несколькими
    активностями и без цели у бьющей активности он отказывает с причиной, а не
    отправляет интент, который движок молча проглотит.

    Цель передаётся по виду активности. Урон требует цель (без неё движок бьёт
    самого героя — замер: Overchannel 60 → 45 HP). Спасбросок цель принимает,
    но не требует: у площадного её нет, а у однoцельного без неё движок только
    подтверждает интент (замер: Hurl Through Hell). Лечение, проверка и утилита
    цель не берут вовсе: переданная цель перенаправляла лечение героя на чужую
    фигуру, а проверке и утилите она не нужна.
    """
    plan = feature_plan(slug)
    if plan is None:
        raise ValueError(f"Умение {slug!r} не найдено ни в пине, ни в записях книги")
    name = plan["name"]
    if plan["status"] in ("master", "blocked"):
        raise ValueError(f"«{name}»: {plan['reason']}")
    if plan["status"] == "choice":
        variants = plan["activities"]
        usable = [row for row in variants if row["status"] == "native"]
        if not activity_id:
            offered = ", ".join(f"{row['name'] or row['kind']} ({row['activity_id']})"
                                for row in usable) or "нет исполнимых вариантов"
            raise ValueError(f"«{name}» предлагает варианты: {offered}")
        chosen = next((row for row in variants if row["activity_id"] == activity_id), None)
        if chosen is None:
            raise ValueError(f"«{name}»: у умения нет активности {activity_id!r}")
        if chosen["status"] != "native":
            raise ValueError(f"«{name}»: {chosen['reason']}")
        plan = dict(plan, **chosen)
    kind = plan.get("kind") or ""
    if kind == "damage" and not target_id:
        raise ValueError(f"«{name}»: активность «{kind}» бьёт по цели — назовите target_id")
    keep_target = kind in ("damage", "save")
    return dict(feature_id=slug, activity_id=plan["activity_id"],
                target_id=target_id if keep_target else None)


# ---------------------------------------------------------------------------
# r175: хост-путь для умений базовых классов, которые движок уже исполняет
#
# r171 разложил 56 правил `adjudication_required` главы классов: у восьми
# эффект в движке уже есть, а до игры правило не доходило, потому что хост не
# проверял условие, не спрашивал выбор и не подставлял параметры интента
# (`tests/test_effect_proof_classes.py`, `tests/test_class_coverage_proof.py` —
# замеры эффекта). Здесь — вторая половина: проверки, которых движок НЕ делает
# (это замерено r171/r175), перечисление выбора для экрана и отказ с причиной
# вместо `CastFailed(invalid_form)`/`CastFailed(activity_not_selected)`/
# «движок молча дал кость врагу».
#
# Ни одна строка здесь не выводит механику заново: дистанции — из `facts`
# правила, формы зверя — из `wild_shape_tier` движка, варианты — из активностей
# умения в пине. Прогон `tests/test_class_features_reach_game.py` проверяет, что
# каждый отказ стоит там, где движок условие не проверяет, и что разрешённый ход
# меняет состояние боя.
# ---------------------------------------------------------------------------
FEET_PER_CELL = 5
UNARMED_STRIKE = "unarmed-strike"
_RAGE_EFFECT_ID = "effect:rage"

#: Русские подписи вариантов `monks-focus`; идентификаторы активностей читаются
#: из записи умения, поэтому подмена данных пина не оставит в хосте старый id.
_FOCUS_LABELS = {
    "Flurry of Blows": "Шквал ударов",
    "Patient Defense (Focus Point)": "Терпеливая оборона (Очко фокуса)",
    "Patient Defense": "Терпеливая оборона (без Очка)",
    "Step of the Wind": "Поступь ветра",
}
_TURN_UNDEAD_NAME = "turn undead"


def _battle(runtime):
    """Живой бой из состояния: проекция и стартовая карта (или пусто)."""
    combat = runtime.get("combat") or {}
    return combat.get("view") or {}, (combat.get("initial") or {}).get("grid") or {}


def _hero_features(runtime):
    return set((runtime.get("definition") or {}).get("features") or ())


def _class_level(runtime, slug):
    classes = (runtime.get("hero") or {}).get("classes") or {}
    try:
        return int(classes.get(slug) or 0)
    except (TypeError, ValueError):
        return 0


def _actor(view, entity_id):
    return next((row for row in view.get("initiative") or []
                 if row.get("entity_id") == entity_id), None)


def _cell(view, entity_id):
    return (view.get("actor_zone") or {}).get(entity_id)


def _alive(row):
    return bool(row) and row.get("is_alive") is not False and (row.get("hp_current") or 0) > 0


def _gap(view, first, second):
    """Дистанция в клетках (шахматная сетка движка), иначе бесконечно."""
    a, b = _cell(view, first), _cell(view, second)
    if not a or not b:
        return None
    ax, ay = (int(part) for part in str(a).split(","))
    bx, by = (int(part) for part in str(b).split(","))
    return max(abs(ax - bx), abs(ay - by))


def _feet(view, grid, first, second):
    """Дистанция в футах по размеру клетки самой сцены, а не по константе.

    `FEET_PER_CELL` — только запасное значение: сцена задаёт `cell_size_ft`
    (движок считает расстояния по нему), и сцена с клеткой 10 фт иначе
    разрешала бы кость вдвое дальше правила.
    """
    gap = _gap(view, first, second)
    if gap is None:
        return None
    return gap * (grid.get("cell_size_ft") or FEET_PER_CELL)


def _allies(view):
    dead = set(view.get("dead_ids") or ())
    return [row for row in view.get("initiative") or []
            if row.get("entity_id") in (view.get("party_ids") or ())
            and row.get("entity_id") != "hero" and row.get("entity_id") not in dead
            and _alive(row)]


def _enemies(view):
    dead = set(view.get("dead_ids") or ())
    return [row for row in view.get("initiative") or []
            if row.get("entity_id") in (view.get("encounter_ids") or ())
            and row.get("entity_id") not in dead and _alive(row)]


def _total_cover(grid, cell):
    return bool(cell) and (grid.get("cover_cells") or {}).get(cell) == "total"


#: SRD 5.2 §Cover — the flat bonus each degree grants to AC and Dexterity
#: saves. The number is the engine's (`dnd5e_engine.spatial.cover_bonus`); this
#: table only exists so a screen sentence can print it next to a miss.
COVER_NAMES = {"half": "половинное", "three_quarters": "три четверти", "total": "полное"}


def cover_degree(grid, origin, target, occupied=()):
    """SRD 5.2 §Cover — степень укрытия между клетками, посчитанная движком.

    Only for the sentence the player reads: hit or miss is the engine's answer,
    and the player still deserves to know why the target's AC was higher. The
    walk and the numbers come from the SAME pinned seam the attack uses
    (``GridTopology.cover_between`` over this grid), so a second rule
    calculation never enters the host. ``occupied`` carries the other live
    combatants' cells: a creature in the way covers at least half (SRD 5.2).
    """
    if not origin or not target or origin == target:
        return "none"
    from dnd5e_engine.specs import GridScene
    from dnd5e_engine.spatial import GridTopology
    try:
        scene = GridScene.model_validate(dict(grid or {}))
    except Exception:
        return "none"
    if not scene.width or not scene.height:
        return "none"
    return GridTopology(scene).cover_between(origin, target, tuple(occupied))


def cover_bonus(degree):
    """SRD 5.2 §Cover — +2 / +5 / невозможная атака, числом из движка."""
    from dnd5e_engine.spatial import cover_bonus as engine_bonus
    return engine_bonus(degree or "none")


def _active_effects(runtime, entity_id="hero"):
    return {effect.get("id"): effect for effect in runtime.get("active_effects") or ()
            if effect.get("target_id") == entity_id and not effect.get("disabled")}


def _heavy_armour(runtime):
    """Надетые тяжёлые доспехи: категория из проекции бойца, иначе из набора.

    Движок Ярость в тяжёлом доспехе не запрещает (замер r175: `EffectApplied`
    при `chain-mail`), поэтому проверка живёт в хосте. Источник — собственное
    поле бойца `worn_armor`: движок пишет туда категорию надетого доспеха
    ('heavy' у кольчуги, `None` без доспеха), и это ровно «надето», а не «есть
    в наборе». Вне боя проекции нет — тогда смотрим набор сборки и называем
    находку слагом; неизвестный слаг не блокирует.
    """
    view = (runtime.get("combat") or {}).get("view") or {}
    hero = _actor(view, "hero") if view else None
    worn = getattr(hero, "get", lambda *_: None)("worn_armor") if hero else None
    if worn == "heavy":
        return ["тяжёлый доспех (по проекции бойца)"]
    if worn:
        return []
    from dnd5e_engine.lib_loader import get_lib_loader
    loader = get_lib_loader()
    found = []
    for slug in (runtime.get("hero") or {}).get("equipment") or ():
        try:
            item = loader.get_item(slug)
        except Exception:  # noqa: BLE001 - неизвестный слаг не повод блокировать
            continue
        if item is not None and str(getattr(item, "armor_category", "") or "") == "heavy":
            found.append(str(slug))
    return found


def _transformed(runtime):
    combat = runtime.get("combat") or {}
    return "hero" in ((combat.get("view") or {}).get("transformations") or {})


def _format_list(rows, key="name", limit=12):
    names = [str(row.get(key) or row.get("id")) for row in rows[:limit]]
    more = "" if len(rows) <= limit else f" и ещё {len(rows) - limit}"
    return "; ".join(names) + more


#: Держим ссылку на загрузчик по ключу кэша: иначе `id()` освобождённого
#: объекта может достаться новому загрузчику, и кэш вернёт зверей прошлого
#: релиза.
_CACHED_LOADERS = {}


def _loader_key(loader):
    key = id(loader)
    _CACHED_LOADERS[key] = loader
    return key


@lru_cache(maxsize=64)
def _beasts(loader_key, level):
    """Звери, проходящие строку таблицы Beast Shapes этого уровня друида.

    `level` — уровень Друида (2…20), а не номер ступени: предел Опасности
    выбирает `wild_shape_tier`, и 5 уровень попадает в ступень 4-го.
    `_known_shapes` зовёт это с уровнями ступеней (2/4/8), потому что каждая
    ступень добавляет формы к уже известным, а гейт «подходит по пределу, но не
    известен» зовёт с настоящим уровнем героя.
    """
    from dnd5e_engine.activities.conjuration import uses_summon_roll_data, wild_shape_tier
    from dnd5e_engine.lib_loader import get_lib_loader
    tier = wild_shape_tier(level)
    if tier is None:
        return ()
    loader = get_lib_loader()
    found = []
    for slug in loader.list_slugs("monsters"):
        monster = loader.get_monster(slug)
        if monster is None or str(monster.creature_type) != "beast":
            continue
        rating = getattr(monster, "cr", None)
        if rating is None or float(rating) > tier.max_cr:
            continue
        movement = getattr(monster, "movement", None)
        if getattr(movement, "fly", None) and not tier.fly_allowed:
            continue
        if uses_summon_roll_data(monster) or getattr(monster, "ac", None) is None:
            continue
        found.append(dict(id=str(slug), name=str(monster.name), cr=float(rating),
                          fly=bool(getattr(movement, "fly", None))))
    return tuple(sorted(found, key=lambda row: (row["cr"], row["name"])))


#: Сколько форм зверя друид ЗНАЕТ: книга PHB 2024 («Дикий облик»), запись
#: `rules_data/v2/chapters/phb-03-classes/05-druid.json` →
#: `rule_parameters.shapes_by_level` (`known=4|6|8`). Уровни совпадают со
#: ступенями таблицы Beast Shapes движка, поэтому каждая ступень приносит
#: прибавку: 2 → 4 формы, 4 → 6 (+2), 8 → 8 (+2). Тест
#: `tests/test_class_features_reach_game.py::test_known_shapes_follow_the_book`
#: сверяет таблицу с записью корпуса, чтобы числа не разошлись молча.
_KNOWN_SHAPES_BY_LEVEL = ((2, 4), (4, 6), (8, 8))


def _known_shapes_rule_text():
    """«4 на 2-м, 6 на 4-м, 8 на 8-м» — из таблицы, а не из литерала в отказе."""
    return ", ".join("%d на %d-м" % (count, step) for step, count in _KNOWN_SHAPES_BY_LEVEL)


@lru_cache(maxsize=64)
def _known_shapes(loader_key, level):
    """Известные формы: каждая ступень таблицы добавляет свои формы.

    Внутри ступени берутся старшие по Опасности звери, поэтому набор
    монотонен: на 4 уровне к четырём формам добавляются две, на 8 — ещё две,
    ранее известные не пропадают
    (`tests/test_class_features_reach_game.py::test_known_shapes_follow_the_book`).
    Если ступень уже разрешает полёт (`wild_shape_tier.fly_allowed`), в
    известных формах держится хотя бы одна летающая: иначе правило книги
    «летать с 8 уровня» остаётся недостижимым, потому что набор хоста
    собирается по Опасности и все летающие звери младше старших наземных.
    Каких именно зверей друид видел и как меняет известную форму после
    Долгого отдыха — решение Мастера (`adjudication`), поэтому хост не
    расширяет список до бестиария.
    """
    from dnd5e_engine.activities.conjuration import wild_shape_tier
    picked = []
    for step_level, count in _KNOWN_SHAPES_BY_LEVEL:
        if step_level > level:
            break
        taken = {row["id"] for row in picked}
        pool = [row for row in _beasts(loader_key, step_level) if row["id"] not in taken]
        pool.sort(key=lambda row: (-row["cr"], row["name"]))
        quota = count - len(picked)
        tier = wild_shape_tier(step_level)
        if tier is not None and tier.fly_allowed and quota > 0 and not any(row["fly"] for row in picked):
            # Рой — не одна зверушка: в форму берётся одиночный летун, рой
            # только если других летающих под ступень нет (решение хоста).
            flyers = [row for row in pool if row["fly"] and not row["id"].startswith("swarm")]
            flyers = flyers or [row for row in pool if row["fly"]]
            if flyers:
                picked.append(flyers[0])
                pool = [row for row in pool if row["id"] != flyers[0]["id"]]
                quota -= 1
        picked.extend(pool[:quota])
        # Экран перечисляет формы по Опасности; ступень добавляет их не по
        # порядку (летун вставляется вперёд), поэтому сортируем после каждой.
        picked.sort(key=lambda row: (-row["cr"], row["name"]))
    return tuple(dict(row) for row in picked)


def known_shapes(runtime):
    """Формы, которые этот друид знает (4/6/8 по книге), а не весь бестиарий.

    Предел Опасности и полёт каждой ступени — из таблицы движка
    (`wild_shape_tier`), количество форм — из записи правила. Экран
    перечисляет только их: до r207 на друиде 5 уровня это было 48 форм и
    около 5,7 с отрисовки, потому что проба доступности гоняет бой на каждого
    кандидата. Строки отдаются копиями, чтобы правка строки вызывающим не
    портила кэш `_beasts`/`_known_shapes`.
    """
    from dnd5e_engine.lib_loader import get_lib_loader
    level = _class_level(runtime, "druid")
    if level < 2:
        return ()
    # Кэш `_known_shapes` хранит dict-строки: наружу отдаём копии, иначе правка
    # строки игроком/экраном испортила бы кэш.
    return tuple(dict(row) for row in _known_shapes(_loader_key(get_lib_loader()), level))


@lru_cache(maxsize=8)
def _focus_options(loader_key):
    from dnd5e_engine.lib_loader import get_lib_loader
    feature = get_lib_loader().get_feature("monks-focus")
    if feature is None:
        return ()
    return tuple(dict(activity_id=str(activity.id),
                      name=_FOCUS_LABELS.get(str(activity.name), str(activity.name)))
                 for activity in feature.activities)


def monks_focus_options():
    """Четыре варианта Сосредоточенности: id из данных, подпись по-русски."""
    from dnd5e_engine.lib_loader import get_lib_loader
    return _focus_options(_loader_key(get_lib_loader()))


@lru_cache(maxsize=8)
def _turn_undead(loader_key):
    from dnd5e_engine.lib_loader import get_lib_loader
    feature = get_lib_loader().get_feature("channel-divinity-cleric")
    if feature is None:
        return None
    for activity in feature.activities:
        if str(activity.name).strip().lower() == _TURN_UNDEAD_NAME:
            return str(activity.id)
    return None


def turn_undead_activity():
    from dnd5e_engine.lib_loader import get_lib_loader
    return _turn_undead(_loader_key(get_lib_loader()))


def class_feature_choices(runtime):
    """(подпись, действие) для умений базового класса, которые хост может отправить.

    Экран показывает только то, что пройдёт проверку условия: «кому» кость
    (союзник в 60 фт и не в полном укрытии), «в какого зверя», «какую нежить в
    30 фт», варианты Сосредоточенности, безоружный удар и бонусный безоружный
    удар монаха. Пустой список — не ошибка: умений этого класса у героя нет или
    бой кончился.
    """
    view, grid = _battle(runtime)
    if not view:
        return []
    features = _hero_features(runtime)
    hero = _actor(view, "hero")
    if hero is None:
        return []
    choices = []
    if "bardic-inspiration" in features:
        for ally in _allies(view):
            feet = _feet(view, grid, "hero", ally["entity_id"])
            if feet is None or feet > 60 or _total_cover(grid, _cell(view, ally["entity_id"])):
                continue
            choices.append((f"Вдохновение: {ally.get('name')}",
                            dict(kind="native_action", operation="use_feature",
                                 feature_id="bardic-inspiration", target_id=ally["entity_id"])))
    if "rage" in features and not _heavy_armour(runtime) and _RAGE_EFFECT_ID not in _active_effects(runtime):
        choices.append(("Ярость", dict(kind="native_action", operation="use_feature",
                                       feature_id="rage")))
    if "wild-shape" in features and not _transformed(runtime):
        for shape in known_shapes(runtime):
            choices.append((f"Дикий облик: {shape['name']} ({shape['id']})",
                            dict(kind="native_action", operation="use_feature",
                                 feature_id="wild-shape", form_id=shape["id"])))
    elif "wild-shape" in features:
        choices.append(("Выйти из облика", dict(kind="native_action", operation="use_feature",
                                              feature_id="wild-shape")))
    if "channel-divinity-cleric" in features:
        activity = turn_undead_activity()
        if activity:
            for foe in _enemies(view):
                feet = _feet(view, grid, "hero", foe["entity_id"])
                if str(foe.get("creature_type") or "").lower() != "undead":
                    continue
                if feet is None or feet > 30:
                    continue
                choices.append((f"Изгнание нежити: {foe.get('name')}",
                                dict(kind="native_action", operation="use_feature",
                                     feature_id="channel-divinity-cleric",
                                     activity_id=activity, target_id=foe["entity_id"])))
    if "monks-focus" in features:
        for option in monks_focus_options():
            choices.append((f"Сосредоточенность: {option['name']}",
                            dict(kind="native_action", operation="use_feature",
                                 feature_id="monks-focus", activity_id=option["activity_id"])))
    if "martial-arts" in features:
        for foe in _enemies(view):
            choices.append((f"Безоружный удар: {foe.get('name')}",
                            dict(kind="native_action", operation="attack",
                                 weapon_id=UNARMED_STRIKE, target_id=foe["entity_id"])))
            if hero.get("bonus_action_available"):
                choices.append((f"Бонусный безоружный удар: {foe.get('name')}",
                                dict(kind="native_action", operation="attack",
                                     weapon_id=UNARMED_STRIKE, target_id=foe["entity_id"],
                                     use_bonus_action=True)))
    return choices


def _gate_bardic(runtime, view, grid, action):
    if not view:
        raise ValueError("«Бардовское вдохновение»: кость выдаётся в бою — вне боя её некому бросить")
    allies = {row["entity_id"]: row for row in _allies(view)}
    target = getattr(action, "target_id", None)
    if not target:
        offers = getattr(action, "target_ids", None) or []
        target = offers[0] if offers else None
    if not allies:
        raise ValueError("«Бардовское вдохновение»: в этом бою нет союзника, которому можно дать кость")
    if not target:
        raise ValueError("«Бардовское вдохновение»: назовите, кому дать кость — "
                         + _format_list([dict(name=row.get("name"), id=key) for key, row in allies.items()]))
    if target not in allies:
        raise ValueError(f"«Бардовское вдохновение»: {target} — не союзник; кость получает другой "
                         "герой (движок принял бы и врага — это и закрывает проверка)")
    row = allies[target]
    feet = _feet(view, grid, "hero", target)
    if feet is None or feet > 60:
        raise ValueError(f"«Бардовское вдохновение»: {row.get('name')} дальше 60 футов"
                         f"{'' if feet is None else f' ({feet} фт)'}")
    if _total_cover(grid, _cell(view, target)):
        raise ValueError(f"«Бардовское вдохновение»: {row.get('name')} в полном укрытии — "
                         "не видно и не слышно")
    # Вторую кость на того же получателя отвергает сам движок
    # (`CastFailed(target_invalid)`): его состояние хранит эффекты только
    # героя, поэтому «у цели уже есть кость» хосту здесь не видно, и проверка
    # не притворяется, что видит.


def _gate_rage(runtime, action):
    worn = _heavy_armour(runtime)
    if worn:
        raise ValueError("«Ярость»: её нельзя включить в тяжёлом доспехе "
                         f"(надето: {', '.join(worn)}); движок этот запрет не делает")
    if _RAGE_EFFECT_ID in _active_effects(runtime):
        raise ValueError("«Ярость» уже действует: повтор не тратит применение")


def _gate_wild_shape(runtime, action):
    level = _class_level(runtime, "druid")
    if level < 2:
        raise ValueError("«Дикий облик»: доступен со 2 уровня Друида")
    # Выход из формы — Бонусное действие без имени зверя (движок:
    # `_leave_wild_shape`). Требовать `form_id` здесь значило бы запереть
    # друида в форме до конца эффекта.
    if _transformed(runtime) and not getattr(action, "form_id", None):
        return
    shapes = known_shapes(runtime)
    if not shapes:
        raise ValueError("«Дикий облик»: в корпусе нет зверей подходящего предела Опасности")
    form = getattr(action, "form_id", None)
    if not form:
        raise ValueError("«Дикий облик»: назовите известного зверя — "
                         + _format_list([dict(id=row["id"], name=f"{row['name']} ({row['id']})")
                                         for row in shapes]))
    if form not in {row["id"] for row in shapes}:
        from dnd5e_engine.lib_loader import get_lib_loader
        eligible = {row["id"] for row in _beasts(_loader_key(get_lib_loader()), level)}
        names = _format_list([dict(id=row["id"], name=f"{row['name']} ({row['id']})")
                              for row in shapes])
        if form in eligible:
            # r207: книга даёт 4/6/8 известных форм, поэтому «подходит по
            # пределу Опасности» ещё не значит «известна герою». Каких зверей
            # друид видел и как меняет известную форму после Долгого отдыха —
            # решает Мастер; хост список не расширяет до бестиария.
            raise ValueError(f"«Дикий облик»: {form} подходит по пределу Опасности, но не входит "
                             f"в {len(shapes)} известных форм Друида {level} уровня "
                             f"(книга: {_known_shapes_rule_text()}); известны: {names}. "
                             "Замену известной формы после Долгого отдыха ведёт Мастер вне хоста: хост свой набор не меняет")
        raise ValueError(f"«Дикий облик»: {form} недоступен на {level} уровне Друида; "
                         "доступны: " + names)


def _gate_turn_undead(view, grid, action):
    if not view:
        raise ValueError("«Изгнание нежити»: работает в бою")
    undead = {row["entity_id"]: row for row in _enemies(view)
              if str(row.get("creature_type") or "").lower() == "undead"}
    target = getattr(action, "target_id", None)
    if not target:
        offers = getattr(action, "target_ids", None) or []
        target = offers[0] if offers else None
    if not undead:
        enemies = _enemies(view)
        types = sorted({str(row.get("creature_type") or "не назван") for row in enemies})
        raise ValueError("«Изгнание нежити»: в этом бою нет Нежити "
                         f"(типы целей: {', '.join(types) or 'целей нет'}) — правило бьёт только нежить; "
                         "тип цели хосту даёт сценарий, и незаполненный тип считается неизвестным, "
                         "а не нежитью")
    if not target:
        raise ValueError("«Изгнание нежити»: назовите цель — "
                         + _format_list([dict(name=f"{row.get('name')} ({key})", id=key)
                                         for key, row in undead.items()]))
    if target not in undead:
        row = _actor(view, target)
        kind = str((row or {}).get("creature_type") or "не назван")
        raise ValueError(f"«Изгнание нежити»: {target} — не Нежить (тип {kind}); "
                         "условие правила — только нежить")
    feet = _feet(view, grid, "hero", target)
    if feet is None or feet > 30:
        raise ValueError(f"«Изгнание нежити»: цель дальше 30 футов"
                         f"{'' if feet is None else f' ({feet} фт)'}")


def _gate_monks_focus(action):
    options = monks_focus_options()
    if not options:
        raise ValueError("«Сосредоточенность»: в данных пина нет вариантов умения")
    chosen = getattr(action, "activity_id", None)
    if not chosen:
        raise ValueError("«Сосредоточенность»: выберите вариант — "
                         + _format_list([dict(id=row["activity_id"],
                                              name=f"{row['name']} ({row['activity_id']})")
                                         for row in options]))
    if chosen not in {row["activity_id"] for row in options}:
        raise ValueError(f"«Сосредоточенность»: варианта {chosen} нет; доступны: "
                         + _format_list([dict(id=row["activity_id"], name=f"{row['name']} ({row['activity_id']})")
                                         for row in options]))


def feature_gate(runtime, action):
    """Хост-проверки умений базового класса до отправки интента.

    Движок исполняет активность, но НЕ проверяет то, что у правила в условии:
    тяжёлый доспех у Ярости, тип и дистанцию цели у Изгнания нежити, 60 фт и
    союзника у Бардовского вдохновения (замеры r171/r175 — в докстроках
    отдельных проверок). Здесь эти условия названы отказом с причиной вместо
    `CastFailed` или вместо хода, который движок молча исполнит неправильно.

    Заменять или дополнять интент эта функция не может намеренно: она только
    отказывает. Параметры (`form_id`, `activity_id`, цель) называет игрок через
    экран или Мастера, иначе хост решал бы за игрока.
    """
    if getattr(action, "kind", "") != "native_action":
        return
    operation = getattr(action, "operation", "")
    view, grid = _battle(runtime)
    features = _hero_features(runtime)
    if operation == "use_feature":
        slug = str(getattr(action, "feature_id", "") or "")
        if slug == "bardic-inspiration" and slug in features:
            _gate_bardic(runtime, view, grid, action)
        elif slug == "rage" and slug in features:
            _gate_rage(runtime, action)
        elif slug == "wild-shape" and slug in features:
            _gate_wild_shape(runtime, action)
        elif slug == "channel-divinity-cleric" and turn_undead_activity() \
                and getattr(action, "activity_id", None) == turn_undead_activity():
            _gate_turn_undead(view, grid, action)
        elif slug == "monks-focus" and slug in features:
            _gate_monks_focus(action)
    elif operation == "cast_spell" and "rage" in features and _RAGE_EFFECT_ID in _active_effects(runtime):
        raise ValueError("«Ярость»: под Яростью нельзя творить заклинания и держать концентрацию")



# ---------------------------------------------------------------------------
# r178: где решение за Мастером или игроком, движок отдаёт подсказку
#
# r171 разложил 56 правил `adjudication_required` главы классов: 12 «Эпических
# даров» кодом не исчислить вовсе (facts пуст, выгоды живут в книге), ещё у
# тридцати правил есть `semantic_questions` — вопрос, на который отвечает
# Мастер. До этой итерации такие записи просто лежали в корпусе: пакет хода
# нёс лист героя и команды движка, а вопрос правила модель не видела.
#
# Здесь — сборка подсказок для пакета интерпретатора (`solo/game.py`,
# `SceneCompiler.command_context`). Три правила, которые держит прогон
# `tests/test_adjudication_hints_to_model.py`:
#
# 1. подсказка «предложи эпический дар» появляется только с 19 уровня и только
#    пока дар не выбран — уровень берётся из состояния героя, а не из головы;
# 2. в пакет идёт вопрос правила вместе с `facts` (числа правила), но только у
#    тех правил, которые совпадают с репликой хода: нерелевантные ходу записи
#    в пакет не попадают, иначе 30 вопросов Мастера раздувают запрос;
# 3. выгоды дара не выдумываются: в записи дара их нет, поэтому подсказка
#    называет выбор игрока и страницу книги, а модель обязана спросить.
# ---------------------------------------------------------------------------
CLASS_CHAPTER = ROOT / "rules_data" / "v2" / "chapters" / "phb-03-classes"
EPIC_BOON_LEVEL = 19
#: Сколько вопросов правил уходит в один ход. Три — потому что вопрос Мастера
#: занимает действие; остальные остаются в корпусе до своей реплики.
HINT_LIMIT = 3
_class_cache: dict = {}

#: Окончания, которые снимаются перед сравнением слов реплики с правилом.
#: Это не морфология, а грубое склеивание падежей: «Проведением» и
#: «Проведение» дают один корень, а «проверка» и «проведение» — разные, чем
#: обрезка первых пяти букв не может похвастаться. Длинные окончания идут
#: первыми, иначе «ением» разберётся как «ем».
_RU_SUFFIXES = sorted(
    ("ениями", "ениях", "ениям", "ением", "ения", "ению", "ении", "ение",
     "аниями", "аниях", "аниям", "анием", "ания", "анию", "ании", "ание",
     "ости", "ость", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
     "ая", "ое", "ые", "ий", "ый", "ей", "ой", "ую", "юю", "ах", "ях", "ов",
     "ев", "ом", "ем", "ам", "ям", "ы", "и", "а", "я", "у", "ю", "е", "о", "ь"),
    key=len, reverse=True)

#: Слово реплики считается «своим» для правила, если оно встречается не больше
#: чем в стольких правилах главы. Порог держится на замере: «атaк» стоит в трёх
#: правилах главы, и с порогом 3 реплика «атакую гоблина» получала три вопроса
#: Мастера, которых ход не касается. Имена правил («Изгнание нежити»,
#: «Контрочарование», «Проведение божественности») стоят в одном-двух.
HINT_DISTINCTIVE_DF = 2


def _stem(word) -> str:
    word = str(word or "").casefold()
    for suffix in _RU_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)]
    return word


def _stems(text) -> set:
    stems = {_stem(word) for word in re.findall(r"[а-яёa-z0-9]+", str(text or "").casefold())}
    return {stem for stem in stems if len(stem) >= 4}


def _rule_words(rule) -> tuple[set, set]:
    """Слова правила: (имя без класса, всё остальное — триггер и вопрос корпуса)."""
    label = str(rule.get("name") or "").split(":", 1)[-1]
    trigger = str(rule.get("id") or "").rsplit(".", 1)[-1].replace("_", " ")
    questions = " ".join(str(row) for row in rule.get("semantic_questions") or [])
    named = _stems(label)
    return named, _stems(trigger) | _stems(questions)


def _class_rules() -> tuple[list[dict], tuple]:
    """Все записи главы классов и отпечаток файлов, по которому они прочитаны.

    Корпус — данные на диске, а не код: его правят генераторы (r147/r169), и
    запомненный навсегда ответ пережил бы их правку. Ключ кэша — имя, mtime и
    размер каждого файла; нет каталога — пустой список, чтобы ход не падал на
    раскладке без корпуса (тогда подсказок просто нет, и это видно по `skipped`).

    Отпечаток возвращается вместе с правилами намеренно: кэш индекса обязан
    штамповаться тем же отпечатком, а не перечитывать общий словарь — иначе
    индекс, построенный по прежним правилам, помечался бы новым отпечатком и
    порча не исправлялась бы никогда (замечание критика r178).
    """
    files = sorted(CLASS_CHAPTER.glob("*.json"))
    stamp = tuple((path.name, probe.st_mtime_ns, probe.st_size)
                  for path in files for probe in (path.stat(),))
    if _class_cache.get("stamp") == stamp and "rules" in _class_cache:
        return _class_cache["rules"], stamp
    rules = []
    for path in files:
        rules.extend(json.loads(path.read_text(encoding="utf-8"))["rules"])
    _class_cache.update(stamp=stamp, rules=rules)
    return rules, stamp


def _hint_index(rules, stamp):
    """Слова каждого вопроса и частота слов по главе — для отбора по реплике."""
    if _class_cache.get("index_stamp") != stamp or "index" not in _class_cache:
        rows, frequency = [], {}
        for rule in rules:
            named, other = _rule_words(rule)
            if not any(str(row).strip() for row in rule.get("semantic_questions") or []):
                continue
            rows.append((rule, named, other))
            for word in named | other:
                frequency[word] = frequency.get(word, 0) + 1
        _class_cache.update(index_stamp=stamp, index=rows, frequency=frequency)
    return _class_cache["index"], _class_cache["frequency"]


def _rule_class(rule) -> str:
    """Класс правила из его id: `phb2024.phb03.bard.yarost` → `bard`."""
    parts = str((rule or {}).get("id") or "").split(".")
    return parts[2] if len(parts) >= 4 and parts[:2] == ["phb2024", "phb03"] else ""


def _hero_level(hero) -> int:
    try:
        return max(0, int((hero or {}).get("level") or 0))
    except (TypeError, ValueError):
        return 0


def _small_facts(facts, *, limit=8, width=140) -> dict:
    """`facts` правила, обрезанные до чисел и коротких строк.

    Пакет хода платный на каждом ходу, поэтому таблицы по уровням и длинные
    фразы сюда не идут целиком: восемь скалярных пар — это всё, что нужно,
    чтобы модель назвала числа правила, а не выдумала их.
    """
    out = {}
    for key, value in (facts or {}).items():
        if len(out) >= limit:
            break
        if isinstance(value, bool) or isinstance(value, (int, float)):
            out[str(key)] = value
        elif isinstance(value, str) and value.strip():
            out[str(key)] = value.strip()[:width]
        elif isinstance(value, dict) and value:
            flat = {str(k): (v if isinstance(v, (int, float, bool)) else str(v)[:40])
                    for k, v in list(value.items())[:6]
                    if isinstance(v, (int, float, bool, str))}
            if flat:
                out[str(key)] = flat
    return out


def _source_brief(rule) -> dict:
    source = (rule or {}).get("source") or {}
    return dict(document=source.get("document_id"), printed_page=source.get("printed_page"),
                pdf_page=source.get("pdf_page"))


#: Что делает движок по записи корпуса — по трём законным статусам главы
#: (`routing.schema.json`: `adjudication_required`, `engine_subset_verified`,
#: `blocked`). Модель должна видеть разницу: у `verified` вопрос правила — это
#: выбор или подтверждение того, что движок уже считает; у `blocked` механики
#: нет вовсе, и называть её числом нельзя.
ENGINE_BY_STATUS = {"engine_subset_verified": "verified", "adapter_verified": "verified",
                    "kernel_verified": "verified", "adjudication_required": "required",
                    "blocked": "blocked"}
ENGINE_TEXT = {"verified": "движок считает эффект, вопрос — выбор или подтверждение",
               "required": "решает Мастер, движок исполняет только названное",
               "blocked": "движок этого не умеет, механику ведёт Мастер словом"}


def _rule_hint(rule, *, klass) -> dict:
    """Один вопрос Мастера: имя правила, вопрос корпуса, числа и что делает движок."""
    questions = [str(row).strip() for row in rule.get("semantic_questions") or [] if str(row).strip()]
    status = (rule.get("readiness") or {}).get("status")
    status = str(status) if status else None
    return dict(id=str(rule.get("id")), name=rule.get("name"), klass=_rule_class(rule),
                status=status, engine=ENGINE_BY_STATUS.get(status or "", "required"),
                decision="master",
                question=questions[0] if questions else "",
                facts=_small_facts(rule.get("facts")),
                source=_source_brief(rule))


def _boon_chosen(hero) -> bool:
    """Дар уже записан? Поле кампании или слаг дара в умениях героя.

    Поле `epic_boon` пока никто не пишет (пути выбора дара нет), поэтому
    проверяются и умения: настоящие слаги даров — `boon-of-truesight`,
    `boon-of-speed` (SRD), и поиск по подстроке «epic» их НЕ находит — это
    замечание критика r178, до правки подсказка висела бы каждый ход.
    """
    if hero.get("epic_boon") or hero.get("epic_boons"):
        return True
    return any(str(slug).casefold().startswith("boon-of")
               for slug in hero.get("features") or [])


def _epic_boon_hint(hero, level, klass, rules):
    """«На 19 уровне дар выбран? какой?» — выбор игрока, выгод в данных нет.

    Выгоды эпического дара живут в отдельной записи книги; в библиотеке пина
    её нет (`epic-boon.json`: `activities: []`, `passive_effects: []` — замер
    r171). Поэтому подсказка не пересказывает выгоды, а называет выбор игрока и
    страницу правила: придуманное число в пакете модель повторит игроку как
    правило книги. Класса нет в корпусе — подсказки тоже нет: сослаться на
    страницу нечем, а подсказка без источника дороже молчания.
    """
    if level < EPIC_BOON_LEVEL or _boon_chosen(hero):
        return None
    rule = next((row for row in rules if _rule_class(row) == klass
                 and str(row.get("id") or "").endswith(".epicheskii_dar")), None)
    if rule is None:
        return None
    page = _source_brief(rule).get("printed_page")
    text = (f"Герой {level} уровня: эпический дар ещё не выбран. Предложи игроку выбрать дар — "
            "выгоды дара записаны в книге" + (f", стр. {page}" if page else "")
            + ", в данных движка их нет. Числа выгод не выдумывай.")
    return dict(id=str(rule.get("id")), name=rule.get("name"), decision="player", level=level,
                klass=klass or None, question="Какой эпический дар берёт игрок?",
                text=text, source=_source_brief(rule))


def adjudication_hints(state, query: str = "", *, limit: int = HINT_LIMIT) -> dict:
    """Подсказки Мастеру для пакета хода: `decision`, вопрос, факты правила.

    `state` — состояние кампании хоста (`campaign['state']`), `query` — реплика
    хода. Подбор правила идёт по совпадению слов реплики с именем правила и его
    триггером: вопрос Изгнания нежити не нужен ходу про короткий отдых, и
    наоборот. Своему классу героя — небольшой приоритет, но не запрет: правило
    чужого класса в реплике всё равно остаётся подсказкой, иначе на вопрос
    «кому отдать кость вдохновения» герой-воин получил бы молчание вместо
    честного «у вашего героя такого умения нет».

    Возвращает словарь, который кладётся в пакет целиком; `skipped` называет,
    сколько релевантных вопросов не вошло из-за предела, — это видно в пакете,
    а не остаётся тайной.
    """
    hero = state.get("hero") if isinstance(state, dict) else None
    hero = hero if isinstance(hero, dict) else {}
    level = _hero_level(hero)
    klass = str(hero.get("class_name") or "").strip()
    rules, stamp = _class_rules()
    epic_boon = _epic_boon_hint(hero, level, klass, rules)
    index, frequency = _hint_index(rules, stamp)

    def scored_for(text):
        words = {word for word in _stems(text) if frequency.get(word, 0) <= HINT_DISTINCTIVE_DF}
        rows = []
        for rule, named, other in index:
            named_hit, other_hit = words & named, words & other
            # Правило узнаётся по СВОЕМУ ИМЕНИ, а не по слову из вопроса: иначе
            # «атакую гоблина» притягивает три правила, где в вопросе стоит
            # «атака», и пакет пухнет от вопросов, которых ход не касается.
            if not named_hit:
                continue
            score = 3 * len(named_hit) + len(other_hit) + (2 if _rule_class(rule) == klass else 0)
            rows.append((score, rule))
        rows.sort(key=lambda row: (-row[0], str(row[1].get("id"))))
        return rows

    # Ход после уточнения приходит склеенным: «<прежняя реплика>\nУточнение
    # игрока: <новая>». Подсказки идут по НОВОЙ части, иначе вопросы прошлой
    # темы вытесняют текущий (замер r178: на четвёртой реплике прогона в пакете
    # стояли правила трёх прежних). Прежняя часть — запасной вариант, когда
    # короткий ответ («да, нежить») сам правила не называет.
    query = str(query or "")
    latest = query.rsplit("Уточнение игрока:", 1)[-1]
    scored = scored_for(latest) or (scored_for(query) if latest != query else [])
    # «Проведение божественности» есть и у жреца, и у паладина: без схлопывания
    # в пакет уходили две почти одинаковые строки, и предел в три вопроса
    # тратился на копию. Одно правило на хвост id, при равном счёте — своё.
    unique = {}
    for score, rule in scored:
        tail = str(rule.get("id") or "").rsplit(".", 1)[-1]
        current = unique.get(tail)
        if current is None or (_rule_class(rule) == klass and _rule_class(current[1]) != klass):
            unique[tail] = (score, rule)
    ordered = sorted(unique.values(), key=lambda row: (-row[0], str(row[1].get("id"))))
    hints = [_rule_hint(rule, klass=klass) for _, rule in ordered[:limit]]
    return dict(
        note=None if not hints and not epic_boon else (
            "Ниже — правила, где решает Мастер или игрок, а не движок. Вопрос правила задай "
            "игроку или подтверди сам; числа бери только из facts и страницы книги. `engine`: "
            "verified — движок считает эффект, required — исполняет лишь названное, blocked — "
            "механики в движке нет. Выгоды, Сл, урон и исход не выдумывай: чего нет в facts, "
            "того движок не считает."),
        hero=dict(klass=klass or None, level=level or None),
        epic_boon=epic_boon,
        rules=hints, skipped=max(0, len(ordered) - limit))
