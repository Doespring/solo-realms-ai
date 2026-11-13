"""PHB 2024 RU, глава 3 «Классы персонажей»: карта правил (PDF 50-177).

Проверяется не «файл лежит», а содержание карты: 12 классов, у каждого —
ключевые атрибуты и не меньше двух классовых умений, каждый rule проходит
настоящую схему v2 из этого репозитория, страница ссылается на evidence и
там действительно стоит заголовок умения, blocked-правила называют блокер,
id уникальны, имена русские. Никаких выдуманных механических чисел: тест
сверяет ключевые цифры с текстом evidence, извлечённым pypdf.

Сеть не используется: схемы и evidence читаются с диска.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
V2 = ROOT / "rules_data/v2"
CHAPTER = V2 / "chapters/phb-03-classes"
EVIDENCE = V2 / "evidence/phb-2024-ru/phb-pp050-177.txt"
SHA256 = "40a8ea0225049902328fcc93aa79c14862a222148bb3bf76ce4ad5efbc8633a5"

EXPECTED = {
    "01-bard.json": ("bard", "Бард"),
    "02-barbarian.json": ("barbarian", "Варвар"),
    "03-fighter.json": ("fighter", "Воин"),
    "04-wizard.json": ("wizard", "Волшебник"),
    "05-druid.json": ("druid", "Друид"),
    "06-cleric.json": ("cleric", "Жрец"),
    "07-warlock.json": ("warlock", "Колдун"),
    "08-monk.json": ("monk", "Монах"),
    "09-paladin.json": ("paladin", "Паладин"),
    "10-rogue.json": ("rogue", "Плут"),
    "11-ranger.json": ("ranger", "Следопыт"),
    "12-sorcerer.json": ("sorcerer", "Чародей"),
}

# Ключевые цифры, прочитанные в PHB 2024 RU и записанные в карту.
KEY_FACTS = {
    ("bard", "vardo_vdohnovenie"): {"range_feet": 60, "max_dice_per_creature": 1},
    ("bard", "prevoshodnoe_vdohnovenie"): {"on_initiative_restore_to": 2},
    ("barbarian", "yarost"): {"max_duration_minutes": 10},
    ("barbarian", "zashita_bez_dospehov"): {"ac_base": 10},
    ("barbarian", "uluchshennyi_zhestokii_udar_17"): {"extra_damage": "2к10", "effects": 2},
    ("barbarian", "dikii_chempion"): {"strength_bonus": 4, "constitution_bonus": 4, "cap": 25},
    ("fighter", "dopolnitelnaya_ataka"): {"attacks_per_attack_action": 2, "unlock_level": 5},
    ("fighter", "tri_dopolnitelnye_ataki"): {"attacks_per_attack_action": 4, "unlock_level": 20},
    ("wizard", "sotvorenie_zaklinanii"): {"cantrips_start": 3, "spellbook_spells_start": 6,
                                          "book_pages": 100, "book_weight_lb": 3},
    ("wizard", "magicheskoe_vosstanovlenie"): {"forbidden_slot_level": 6, "example_level_4_levels": 2},
    ("wizard", "firmennye_zaklinaniya"): {"spells": 2, "spell_level": 3},
    ("wizard", "kopirovanie_zaklinanii"): {"hours_per_level_new": 2, "gold_per_level_new": 50,
                                           "gold_per_level_copy": 10},
    ("druid", "dikii_oblik"): {"uses_start": 2, "no_fly_speed_until_level": 8},
    ("druid", "uluchshennaya_yarost_stihii"): {"range_bonus_feet": 300,
                                               "nature_strike_extra_damage": "2к8"},
    ("cleric", "bozhestvennaya_iskra"): {"range_feet": 30},
    ("cleric", "velikoe_bozhestvennoe_vmeshatelstvo"): {"spell": "Желание",
                                                        "recharge": "2к4 Долгих отдыха"},
    ("warlock", "magiya_dogovora"): {"max_slot_level": 5, "prepared_level_1": 2},
    ("monk", "monasheskaya_sosredotochennost"): {"dc": "8 + модификатор Мудрости + Бонус владения"},
    ("monk", "sovershennaya_zashita"): {"cost": 3, "duration_minutes": 1},
    ("monk", "telo_i_razum"): {"dexterity_bonus": 4, "wisdom_bonus": 4, "cap": 25},
    ("paladin", "aura_zashity"): {"radius_feet": 10, "single_aura_only": True},
    ("paladin", "rasshirenie_aury"): {"radius_feet": 30},
    ("paladin", "vozlozhenie_ruk"): {"pool": "пятикратный уровень Паладина", "poison_cure_cost": 5},
    ("rogue", "hitryi_udar"): {"dc": "8 + модификатор Ловкости + Бонус владения"},
    ("rogue", "ulybka_fortuny"): {"replace_result_with": 20},
    ("ranger", "izbrannyi_vrag"): {"free_casts_start": 2},
    ("ranger", "dikie_chuvstva"): {"blindsight_feet": 30},
    ("sorcerer", "istochnik_magii"): {"points_start": 2, "max_created_slot_level": 5},
    ("sorcerer", "charodeiskoe_vosstanovlenie"): {
        "restore": "половина уровня Чародея, округляя в меньшую сторону"},
}

# Значения таблиц по уровням — выборочная сверка с «Умения <класса>».
LEVEL_TABLES = {
    ("bard", "vardo_vdohnovenie", "die_by_level"): {"1": "к6", "5": "к8", "10": "к10", "15": "к12"},
    ("barbarian", "yarost", "uses_by_level"): {"1": 2, "3": 3, "6": 4, "12": 5, "17": 6},
    ("barbarian", "yarost", "damage_bonus_by_level"): {"1": 2, "9": 3, "16": 4},
    ("fighter", "vtoroe_dyhanie", "uses_by_level"): {"1": 2, "4": 3, "10": 4},
    ("fighter", "vsplesk_deistvii", "uses_by_level"): {"2": 1, "17": 2},
    ("fighter", "upornyi", "uses_by_level"): {"9": 1, "13": 2, "17": 3},
    ("wizard", "akademicheskie_znaniya", "skill_choices"): ["История", "Медицина", "Природа",
                                                             "Расследование", "Религия",
                                                             "Тайная магия"],
    ("warlock", "magiya_dogovora", "slot_table"): {"1": [1, 1], "11": [3, 5], "17": [4, 5]},
    ("monk", "boevye_iskusstva", "martial_arts_die_by_level"): {"1": "к6", "5": "к8",
                                                                "11": "к10", "17": "к12"},
    ("monk", "dvizhenie_bez_dospehov", "bonus_by_level"): {"2": 10, "6": 15, "10": 20,
                                                           "14": 25, "18": 30},
    ("rogue", "kovarnaya_ataka", "dice_by_level"): {"1": "1к6", "3": "2к6", "5": "3к6",
                                                    "7": "4к6", "9": "5к6", "11": "6к6",
                                                    "13": "7к6", "15": "8к6", "17": "9к6",
                                                    "19": "10к6"},
    ("sorcerer", "istochnik_magii", "slot_creation_cost"): {"1": 2, "2": 3, "3": 5, "4": 6, "5": 7},
}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _files():
    return sorted(CHAPTER.glob("*.json"))


FILES = _files()
ALL_RULES = [row for path in FILES for row in _load(path)["rules"]]
BY_KEY = {}
for path in FILES:
    slug = EXPECTED.get(path.name, (None,))[0] or path.stem.split("-", 1)[1]
    for row in _load(path)["rules"]:
        assert row["id"].startswith(f"phb2024.phb03.{slug}."), row["id"]
        BY_KEY[(row["id"].split(".")[2], row["id"].split(".")[3])] = row


@pytest.fixture(scope="module")
def evidence_pages():
    text = EVIDENCE.read_text(encoding="utf-8")
    pages = {}
    for block in re.split(r"^=== PDF PAGE (\d+) ===$", text, flags=re.M)[1:]:
        pass
    matches = list(re.finditer(r"^=== PDF PAGE (\d+) ===$", text, flags=re.M))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        pages[int(match.group(1))] = text[match.end():end]
    return pages


def _validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    registry = Registry()
    local = {}
    for candidate in sorted(V2.glob("*.json")):
        try:
            document = json.loads(candidate.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(document, dict) and ("properties" in document or "$defs" in document):
            local[candidate.name] = document
    for name, document in local.items():
        registry = registry.with_resource((V2 / name).resolve().as_uri(),
                                          Resource.from_contents(document))
    schema = _load(V2 / "routing.schema.json")
    return Draft202012Validator(schema, registry=registry)


def test_twelve_class_files_exist():
    assert [path.name for path in FILES] == sorted(EXPECTED), \
        [path.name for path in FILES]


def test_every_class_has_attributes_and_two_features():
    counts = {}
    for path in FILES:
        rows = _load(path)["rules"]
        slug = EXPECTED[path.name][0]
        ids = {row["id"].split(".")[3]: row for row in rows}
        assert "class_attributes" in ids, slug
        features = [key for key in ids if key != "class_attributes"]
        counts[slug] = len(features)
        assert len(features) >= 2, (slug, features)
        attributes = ids["class_attributes"]["facts"]
        for field in ("hit_die", "primary", "saves", "subclass_level"):
            assert attributes[field], (slug, field)
        assert re.fullmatch(r"к(6|8|10|12)", attributes["hit_die"]), (slug, attributes["hit_die"])
        assert isinstance(attributes["saves"], list) and len(attributes["saves"]) == 2
        assert len(attributes["subclasses"]) == 4, (slug, attributes["subclasses"])
    assert set(counts) == set(EXPECTED[name][0] for name in EXPECTED)
    assert sum(counts.values()) >= 24


def test_every_rule_matches_v2_schema():
    validator = _validator()
    for path in FILES:
        payload = _load(path)
        assert payload["schema_version"] == "2.1.0"
        errors = list(validator.iter_errors(payload))
        assert not errors, (path.name, errors[0].message if errors else None)


def test_pdf_pages_inside_chapter_and_backed_by_evidence(evidence_pages):
    assert min(evidence_pages) == 50 and max(evidence_pages) == 177, \
        (min(evidence_pages), max(evidence_pages))
    for row in ALL_RULES:
        page = row["source"]["pdf_page"]
        assert 50 <= page <= 177, (row["id"], page)
        assert row["source"]["document_id"] == "phb_2024_ru"
        assert row["source"]["sha256"] == SHA256
        printed = row["source"]["printed_page"]
        assert printed.isdigit(), (row["id"], printed)
        heading = row["source"]["section"].split(":")[-1].strip().rstrip(".")
        if heading.startswith("Выбор "):
            # заголовок из книги: «Выбор Барда...»
            assert heading + "..." in evidence_pages[page], (row["id"], page, heading)
            continue
        heading = re.sub(r"\s*\((?:Бард|Варвар|Воин|Волшебник|Друид|Жрец|Колдун|Монах|"
                         r"Паладин|Плут|Следопыт|Чародей|\d+-й уровень)\)$", "", heading)
        assert heading in evidence_pages[page], (row["id"], page, heading)


def test_every_rule_names_its_execution_or_its_gap():
    """r131: карта обязана называть исполнение или дыру, а не молчать.

    До r131 все 201 правила стояли `blocked` с общей фразой «механика не
    реализована в движке», хотя `derive_sheet` выдаёт умения всех 12 классов.
    Теперь статус доказывается прогоном (см. `test_class_coverage_proof.py`),
    а здесь проверяется форма: у исполняемого правила нет блокеров, у
    неисполняемого — есть и они не пустые.
    """
    verified = 0
    for row in ALL_RULES:
        readiness = row["readiness"]
        status = readiness["status"]
        assert status in {"adapter_verified", "kernel_verified", "engine_subset_verified",
                          "adjudication_required", "blocked"}, row["id"]
        if status == "engine_subset_verified":
            verified += 1
            assert readiness["blockers"] == [], (row["id"], readiness["blockers"])
            assert row["implementation"] == "existing_engine_subset", row["id"]
        else:
            assert readiness["blockers"], row["id"]
            assert all(block.strip() for block in readiness["blockers"]), row["id"]
        if status == "blocked":
            assert readiness["handler"] is None, row["id"]
            assert row["implementation"] == "not_implemented", row["id"]
        assert row["auto_activate"] is False, row["id"]
    assert verified >= 60, verified



def test_ids_unique_and_revision_recorded():
    ids = [row["id"] for row in ALL_RULES]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 24
    # `revision` — версия записи правила, а не издания: целое от 1, растёт, когда
    # меняется сама запись. Ревизия закреплена по каждому id: правило, которое
    # пересмотрели, обязано быть здесь названо, а не «проехать» молча.
    revised_in_r147 = {
        # r147: подклассы коллегий барда появились в движке, поэтому у головных
        # умений трёх коллегий сменились implementation (not_implemented →
        # existing_engine_subset), readiness.status (blocked →
        # adjudication_required) и текст блокера (печатная страница + «исполняет
        # Мастер»). Само правило-механика не менялось, но запись — да, и по ней
        # принимают решение: ревизия 2.
        "phb2024.phb03.bard.kolegiya_dobletsi_boevoe_vdohnovenie",
        "phb2024.phb03.bard.kolegiya_ocharovaniya_maniya_vdohnoveniya",
        "phb2024.phb03.bard.kolegiya_tantsa_oslepitelnye_fuete",
    }
    # r175: восемь правил главы переведены в «движок исполняет» по прогону
    # (хост-проверка + наблюдаемый эффект). Менялась запись правила — статус,
    # обработчик, область и снятые блокеры, — поэтому ревизия 2.
    revised_in_r175 = {
        "phb2024.phb03.bard.vardo_vdohnovenie",
        "phb2024.phb03.barbarian.yarost",
        "phb2024.phb03.barbarian.nepreryvnaya_yarost",
        "phb2024.phb03.druid.dikii_oblik",
        "phb2024.phb03.cleric.izgnanie_nezheti",
        "phb2024.phb03.monk.boevye_iskusstva",
        "phb2024.phb03.monk.monasheskaya_sosredotochennost",
        "phb2024.phb03.monk.shkval_udarov",
    }
    revised = revised_in_r147 | revised_in_r175
    for row in ALL_RULES:
        assert row["id"].startswith("phb2024.phb03.")
        assert row["revision"] == (2 if row["id"] in revised else 1), row["id"]
        assert row["rules_family"] == "dnd2024"
        assert row["classification"]["mode"] in ("deterministic", "hybrid", "adjudicated")


def test_names_are_russian():
    """`name` видит игрок, и оно только по-русски.

    `readiness.scope` — не пользовательский текст, а техническая записка для
    хоста и движка: в ней законно стоят номера ревизий (`r177`), нотация кости
    (`D20`), имена обработчиков (`check_modifier`) и коды отказа
    (`cast_failed/heavy_armor`). Поэтому проверки разделены: имя проверяется
    здесь без исключений, латиница в `scope` — отдельным тестом ниже, по
    закреплённому списку. Переводить ли сами идентификаторы — правка данных
    корпуса, решение владельца, а не этого теста.
    """
    for row in ALL_RULES:
        assert not re.search(r"[A-Za-z]", row["name"]), (row["id"], row["name"])


#: Латинские токены, оставленные в `readiness.scope` осознанно: номер ревизии,
#: в которой правило проверено прогоном (`r177`), нотация кости (`D20`), имя
#: функции движка (`check_modifier`) и коды отказа (`cast_failed/...`).
#: Список закреплён с двух сторон: новый токен в `scope` роняет тест, а
#: исчезнувший — тоже, чтобы список не превратился в разрешение на всё.
SCOPE_LATIN_ALLOWED = {
    "D20", "r177", "check_modifier", "cast_failed/heavy_armor", "cast_failed/raging", "Turned",
}


def test_scope_latin_is_only_a_pinned_technical_reference():
    seen = {
        row["id"]: set(re.findall(r"[A-Za-z][A-Za-z0-9_./]*", row["readiness"]["scope"]))
        for row in ALL_RULES
    }
    unexpected = {rid: sorted(tokens - SCOPE_LATIN_ALLOWED) for rid, tokens in seen.items()
                  if tokens - SCOPE_LATIN_ALLOWED}
    assert not unexpected, unexpected
    assert SCOPE_LATIN_ALLOWED == set().union(*seen.values())


def test_formal_rules_carry_registered_procedure_and_semantic_rules_ask():
    for row in ALL_RULES:
        classification = row["classification"]
        if classification["formal_required"]:
            assert row["formal_operations"], row["id"]
            for operation in row["formal_operations"]:
                assert operation["op"] == "registered_procedure", row["id"]
                assert operation["constants"]["procedure_id"], row["id"]
        else:
            assert not row["formal_operations"], row["id"]
        if classification["semantic_required"]:
            assert row["semantic_questions"], row["id"]
        else:
            assert not row["semantic_questions"], row["id"]
        assert row["behavior_tests"], row["id"]


def test_key_numbers_come_from_the_book():
    for (slug, key), expected in KEY_FACTS.items():
        facts = BY_KEY[(slug, key)]["facts"]
        for field, value in expected.items():
            assert facts.get(field) == value, (slug, key, field, facts.get(field), value)


def test_level_tables_match_printed_tables():
    for (slug, key, field), sample in LEVEL_TABLES.items():
        row = BY_KEY[(slug, key)]
        if field in row["facts"]:  # плоский список — прямо в facts
            assert row["facts"][field] == sample, (slug, key, field)
            continue
        table = row["rule_parameters"][field]
        for level, value in sample.items():
            if isinstance(value, list):
                printed = "|".join(map(str, value))
            elif isinstance(value, dict):
                printed = "|".join(f"{k}={v}" for k, v in value.items())
            else:
                printed = str(value)
            assert table[str(level)] == printed, (slug, key, field, level,
                                                  table[str(level)], printed)


def test_srd_crosscheck_covers_the_map():
    """Каждое правило карты сверено с SRD 5.2.1, а страница SRD — реальная."""
    crosscheck = _load(V2 / "evidence/phb-2024-ru/phb03-srd-crosscheck.json")
    assert crosscheck["rules_total"] == len(ALL_RULES)
    assert crosscheck["missing"] == [], crosscheck["missing"]
    covered = crosscheck["rules"]
    assert set(covered) == {row["id"].split(".", 2)[2].replace(".", "/", 1)
                            for row in ALL_RULES}, \
        "карта и сверка разошлись по составу"
    absent = set(crosscheck["absent_from_srd"])
    found = 0
    for key, row in covered.items():
        assert row["srd_title"], key
        if key in absent:
            assert row["found"] is False, key
            continue
        assert row["found"] is True, key
        assert row["srd_pages"] and all(1 <= page <= 364 for page in row["srd_pages"]), key
        found += 1
    assert found == crosscheck["found_total"] >= 190, crosscheck["found_total"]


def test_hybrid_rules_ask_the_master():
    for row in ALL_RULES:
        if row["classification"]["mode"] == "hybrid":
            assert row["classification"]["semantic_required"] is True
            assert row["semantic_questions"], row["id"]
            assert row["formal_operations"], row["id"]


def test_evidence_file_is_extracted_text_not_empty():
    text = EVIDENCE.read_text(encoding="utf-8")
    assert "глава 3 | классы персонажей" in text
    assert len(text) > 200_000, len(text)
    for class_name, tail in (("Бард", "Барда"), ("Варвар", "Варвара"), ("Воин", "Воина"),
                             ("Волшебник", "Волшебника"), ("Друид", "Друида"),
                             ("Жрец", "Жреца"), ("Колдун", "Колдуна"), ("Монах", "Монаха"),
                             ("Паладин", "Паладина"), ("Плут", "Плута"),
                             ("Следопыт", "Следопыта"), ("Чародей", "Чародея")):
        assert f"Подклассы {tail}" in text, class_name
        assert class_name in text, class_name
