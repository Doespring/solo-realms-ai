"""Герой: справочники, лист и броски — всё через движок Nat20.

Сайт ничего не считает сам: модификаторы, хиты, класс брони, навыки,
спасброски и исход проверки приходят из ``dnd5e_engine``. Здесь только
русские подписи и перевод между формой и спецификацией сборки.
"""
from __future__ import annotations

import json
import random

import dnd5e_engine as engine
from dnd5e_srd_data.loader import BundledAssetLoader

LOADER = BundledAssetLoader()

SPECIES = {
    "human": "Человек",
    "elf-high": "Высокий эльф",
    "elf-wood": "Лесной эльф",
    "elf-drow": "Дроу",
    "dwarf": "Дварф",
    "halfling": "Полурослик",
    "gnome-forest": "Лесной гном",
    "gnome-rock": "Каменный гном",
    "goliath": "Голиаф",
    "orc": "Орк",
    "dragonborn": "Драконорождённый",
    "tiefling-infernal": "Тифлинг, инфернальный",
    "tiefling-abyssal": "Тифлинг, бездны",
    "tiefling-chthonic": "Тифлинг, хтонический",
}

CLASSES = {
    "fighter": "Воин",
    "wizard": "Волшебник",
    "rogue": "Плут",
    "cleric": "Жрец",
    "ranger": "Следопыт",
    "bard": "Бард",
    "paladin": "Паладин",
    "barbarian": "Варвар",
    "monk": "Монах",
    "druid": "Друид",
    "warlock": "Колдун",
    "sorcerer": "Чародей",
}

CLASS_HINT = {
    "fighter": "стойкий боец, много хитов, оружие и щит",
    "wizard": "книга заклинаний, мало хитов, самый широкий набор магии",
    "rogue": "скрытность, точный удар, замки и карманы",
    "cleric": "лечение и поддержка, божественная магия",
    "ranger": "следопыт, лук, природа и звери",
    "bard": "убеждение, поддержка, немного всего",
    "paladin": "тяжёлый доспех, кара, защита союзников",
    "barbarian": "ярость, много хитов, мало брони",
    "monk": "без брони и оружия, скорость и удары",
    "druid": "природа, превращения, заклинания",
    "warlock": "сила от покровителя, мало ячеек, но частые",
    "sorcerer": "врождённая магия, гибкие заклинания",
}

BACKGROUNDS = {
    "soldier": "Солдат",
    "criminal": "Преступник",
    "sage": "Мудрец",
    "acolyte": "Послушник",
}

ABILITIES = {
    "strength": "Сила",
    "dexterity": "Ловкость",
    "constitution": "Телосложение",
    "intelligence": "Интеллект",
    "wisdom": "Мудрость",
    "charisma": "Харизма",
}

SKILLS = {
    "acrobatics": ("Акробатика", "dexterity"),
    "animal_handling": ("Обращение с животными", "wisdom"),
    "arcana": ("Магия", "intelligence"),
    "athletics": ("Атлетика", "strength"),
    "deception": ("Обман", "charisma"),
    "history": ("История", "intelligence"),
    "insight": ("Проницательность", "wisdom"),
    "intimidation": ("Запугивание", "charisma"),
    "investigation": ("Анализ", "intelligence"),
    "medicine": ("Медицина", "wisdom"),
    "nature": ("Природа", "intelligence"),
    "perception": ("Внимательность", "wisdom"),
    "performance": ("Выступление", "charisma"),
    "persuasion": ("Убеждение", "charisma"),
    "religion": ("Религия", "intelligence"),
    "sleight_of_hand": ("Ловкость рук", "dexterity"),
    "stealth": ("Скрытность", "dexterity"),
    "survival": ("Выживание", "wisdom"),
}

# Стандартный набор: сумма 72, до раскидки по характеристикам.
STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]
ABILITY_MIN, ABILITY_MAX, ABILITY_POINTS = 8, 15, 27


def point_cost(score: int) -> int:
    """Стоимость характеристики в очках (покупка очками, 27 очков)."""
    steps = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
    return steps.get(int(score), 99)


def points_used(abilities: dict[str, int]) -> int:
    return sum(point_cost(v) for v in abilities.values())


ABBR = {
    "strength": "СИЛ", "dexterity": "ЛОВ", "constitution": "ТЕЛ",
    "intelligence": "ИНТ", "wisdom": "МУД", "charisma": "ХАР",
}


# ── предметы и спасброски на русском ─────────────────────────────────────────
SAVE_SHORT = {"str": "СИЛ", "dex": "ЛОВ", "con": "ТЕЛ",
              "int": "ИНТ", "wis": "МУД", "cha": "ХАР"}

ITEM_RU = {
    "spear": "копьё", "shortbow": "короткий лук", "longbow": "длинный лук",
    "arrows": "стрелы", "quiver": "колчан", "dagger": "кинжал",
    "longsword": "длинный меч", "shortsword": "короткий меч", "greatsword": "двуручный меч",
    "battleaxe": "боевой топор", "handaxe": "топорик", "mace": "булава",
    "warhammer": "боевой молот", "quarterstaff": "посох", "club": "дубина",
    "rapier": "рапира", "scimitar": "ятаган", "sickle": "серп", "sling": "праща",
    "dart": "дротик", "javelin": "метательное копьё", "light-crossbow": "лёгкий арбалет",
    "shield": "щит", "chain-mail": "кольчуга", "scale-mail": "чешуйчатый доспех",
    "leather-armor": "кожаный доспех", "studded-leather-armor": "клёпаный кожаный доспех",
    "hide-armor": "шкурный доспех", "breastplate": "кираса", "plate-armor": "латы",
    "padded-armor": "стёганый доспех", "holy-symbol": "священный символ",
    "spellbook": "книга заклинаний", "component-pouch": "мешочек с компонентами",
    "arcane-focus": "магический фокус", "druidic-focus": "друидический фокус",
    "thieves-tools": "воровские инструменты", "lute": "лютня", "flute": "флейта",
    "healers-kit": "лекарский набор", "rope": "верёвка", "torch": "факел",
    "waterskin": "мех с водой", "rations": "паёк", "bedroll": "спальный мешок",
    "tinderbox": "огниво", "potion-of-healing": "зелье лечения",
    "explorers-pack": "набор путешественника", "dungeoneers-pack": "набор подземельщика",
    "priests-pack": "набор жреца", "burglars-pack": "набор взломщика",
    "scholars-pack": "набор учёного", "entertainers-pack": "набор артиста",
    "backpack": "рюкзак", "candle": "свеча", "lamp": "лампа", "oil": "масло",
    "string": "тетива", "case": "футляр", "map": "карта", "book": "книга",
}


def item_name(slug: str) -> str:
    """Русское название предмета; если нет — английское из движка, а не слаг."""
    if slug in ITEM_RU:
        return ITEM_RU[slug]
    try:
        return str(LOADER.get_item(slug).name)
    except Exception:
        return slug.replace("-", " ")


def equipment_list(sheet: dict) -> list[tuple[str, int]]:
    return [(item_name(str(i)), int(n)) for i, n in (sheet.get("equipment") or [])]


def modifier(score: int) -> int:
    return (int(score) - 10) // 2


def build_sheet(species: str, klass: str, background: str, level: int,
                abilities: dict[str, int], name: str = "Герой") -> dict:
    """Собрать лист через движок и вернуть словарь для БД и экрана."""
    spec = engine.CharacterBuildSpec(
        species_slug=species,
        class_slug=klass,
        level=int(level),
        ability_scores=engine.AbilityScores(**{k: int(v) for k, v in abilities.items()}),
        background_slug=background,
    )
    sheet = engine.derive_sheet(spec, loader=LOADER)
    return {
        "name": name,
        "hp_max": sheet.hp_max,
        "hp_current": sheet.hp_max,
        "ac": sheet.ac,
        "proficiency_bonus": sheet.proficiency_bonus,
        "speed": sheet.base_speed,
        "passive_perception": sheet.passive_perception,
        "saves": sorted(sheet.save_proficiencies),
        "skills": sorted(sheet.skill_proficiencies),
        "expertise": sorted(sheet.skill_expertise),
        "hit_dice": sheet.hit_dice,
        "features": list(sheet.features)[:12],
        "equipment": [[str(i), int(n)] for i, n in sheet.starting_equipment],
        "modifiers": {k: modifier(v) for k, v in abilities.items()},
    }


def skill_modifier(abilities: dict[str, int], sheet: dict, skill: str) -> int:
    _, ability = SKILLS[skill]
    mod = modifier(abilities[ability])
    if skill in sheet.get("skills", []):
        mod += sheet.get("proficiency_bonus", 2)
        if skill in sheet.get("expertise", []):
            mod += sheet.get("proficiency_bonus", 2)
    return mod


def resolve(abilities: dict[str, int], sheet: dict, *, skill: str | None = None,
            ability: str | None = None, dc: int = 15, kind: str = "skill",
            advantage: bool = False, disadvantage: bool = False,
            seed: int | None = None) -> dict:
    """Бросок через движок. Возвращает готовый результат для экрана."""
    spec = engine.CheckSpec(
        kind=kind,
        ability_scores={k: int(v) for k, v in abilities.items()},
        proficient_skills=tuple(sheet.get("skills", [])),
        proficient_saves=tuple(sheet.get("saves", [])),
        proficiency_bonus=int(sheet.get("proficiency_bonus", 2)),
        expertise_skills=tuple(sheet.get("expertise", [])),
        skill=skill,
        ability=ability,
        dc=int(dc),
        advantage=advantage,
        disadvantage=disadvantage,
        rng=random.Random(seed) if seed is not None else random.Random(),
    )
    res = engine.resolve_check(spec)
    return {
        "kind": res.kind,
        "skill": res.skill,
        "ability": res.ability,
        "natural": res.natural_roll,
        "modifier": res.modifier,
        "total": res.roll_total,
        "dc": res.dc,
        "success": res.success,
        "proficient": res.is_proficient,
        "label": (SKILLS.get(res.skill, (ABILITIES.get(res.ability or "", res.skill or "")))[0]
                  if res.skill else ABILITIES.get(res.ability or "", "")),
    }


def sheet_json(hero_row) -> dict:
    return json.loads(hero_row["sheet"])


def abilities_json(hero_row) -> dict:
    return json.loads(hero_row["abilities"])


def sheet_json_row(row) -> dict:
    """Лист из строки кампании (join: hero_sheet)."""
    return json.loads(row["hero_sheet"])


def abilities_json_row(row) -> dict:
    return json.loads(row["hero_abilities"])


def summary_row(row) -> dict:
    """Короткая карточка из строки кампании (join: hero_*)."""
    return {
        "id": row["hero_id"],
        "name": row["hero_name"],
        "klass": CLASSES.get(row["hero_class"], row["hero_class"]),
        "level": row["hero_level"],
    }


def summary(hero_row) -> dict:
    """Короткая карточка героя для списков."""
    sheet = sheet_json(hero_row)
    return {
        "id": hero_row["id"],
        "name": hero_row["name"],
        "species": SPECIES.get(hero_row["species"], hero_row["species"]),
        "klass": CLASSES.get(hero_row["klass"], hero_row["klass"]),
        "background": BACKGROUNDS.get(hero_row["background"], hero_row["background"]),
        "level": hero_row["level"],
        "hp": sheet.get("hp_max"),
        "ac": sheet.get("ac"),
    }
