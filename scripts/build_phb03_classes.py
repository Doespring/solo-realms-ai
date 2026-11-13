"""Сборка карты правил PHB 2024 RU, глава 3 «Классы персонажей» (PDF 50-177).

Источник — текст, извлечённый pypdf в
``rules_data/v2/evidence/phb-2024-ru/phb-pp050-177.txt``. Скрипт НЕ сочиняет
механику: все числа в ``facts`` проставлены вручную по прочитанному тексту,
а страница каждого умения берётся из evidence по заголовку
``<N>-й уровень: <Умение>`` и сверяется с ожидаемой.

Каждый класс пишется отдельным файлом
``rules_data/v2/chapters/phb-03-classes/NN-<class>.json``.
Все правила заблокированы: в движке Nat20 нет ни одного хендлера для
классовых умений, а свежесть no-ops не выдаётся.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "rules_data/v2/evidence/phb-2024-ru/phb-pp050-177.txt"
OUT_DIR = ROOT / "rules_data/v2/chapters/phb-03-classes"
SHA256 = "40a8ea0225049902328fcc93aa79c14862a222148bb3bf76ce4ad5efbc8633a5"
FIRST_PAGE, LAST_PAGE = 50, 177

ENGINE = "механика не реализована в движке"

# --------------------------------------------------------------------------
# Данные классов. Порядок — порядок файлов, заданный заданием.


def fact(key, source, kind="procedure", mode="deterministic", ops=("registered_procedure",),
         questions=(), consts=None, duration="per_action", deps=None, resource=None,
         note=None, deploys=False, resource_id=None):
    """Одна запись: умение класса с механическими числами из книги.

    ``source`` — (pdf_page, printed_page, раздел) или название умения, тогда
    страница ищется в evidence по заголовку (``deploys=True``).
    """
    return dict(key=key, source=source, kind=kind, mode=mode, ops=list(ops),
                questions=list(questions), consts=consts or {}, duration=duration,
                deps=list(deps or ()), resource=resource, resource_id=resource_id,
                note=note, deploys=deploys)


CLASSES = [
    dict(slug="bard", file="01-bard.json", name="Бард", pages=(51, 60), start=52,
         genitive="Барда",
         attributes=dict(hit_die="к6", primary="Харизма", saves=["Ловкость", "Харизма"],
                         skills="любые три навыка"[:19], weapons="Простое оружие",
                         tools="три Музыкальных инструмента", armor="Лёгкие доспехи"),
         subclass_level=3,
         subclasses=["Коллегия доблести", "Коллегия знаний", "Коллегия очарования", "Коллегия танца"],
         resources=["Кость бардовского вдохновения: к6, к8 на 5-м, к10 на 10-м, к12 на 15-м уровне",
                    "ячейки заклинаний", "подготовленные заклинания 1-го уровня и выше"],
         features=[
             fact("vardo_vdohnovenie", (52, "51", "Бардовское вдохновение"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какому существу в пределах 60 футов, которое видит или слышит Барда, "
                             "Мастер подтверждает Кость бардовского вдохновения?"],
                  consts=dict(range_feet=60, max_dice_per_creature=1, bonus_action=True,
                              duration="1 час", die_by_level={"1": "к6", "5": "к8", "10": "к10", "15": "к12"},
                              uses="модификатор Харизмы, минимум 1",
                              recharge="Долгий отдых"),
                  deps=["phb2024.phb01.ability_modifier"],
                  resource="Кость бардовского вдохновения", resource_id="bardic_inspiration_die"),
             fact("sotvorenie_zaklinanii", (52, "51", "Сотворение заклинаний (Бард)"),
                  consts=dict(cantrips_start=2, cantrip_levels=[4, 10], spells_start=2,
                              prepared_level_1=4, spellcasting_ability="Харизма",
                              spellbook=False),
                  deps=["phb2024.phb01.spell_slots"]),
             fact("ekspertnost", (53, "52", "Экспертность (Бард)"),
                  consts=dict(skills=2, extra_at_level=9),
                  deps=["phb2024.phb01.expertise"]),
             fact("master_na_vse_ruki", (53, "52", "Мастер на все руки"),
                  consts=dict(bonus="половина Бонуса владения", rounding="floor"),
                  deps=["phb2024.phb01.rounding"]),
             fact("podklass_barda", (53, "52", "Подкласс Барда"),
                  consts=dict(subclass_level=3,
                              subclasses=["Коллегия доблести", "Коллегия знаний",
                                          "Коллегия очарования", "Коллегия танца"])),
             fact("uvelichenie_harakteristik", (54, "53", "Увеличение характеристик"),
                  deps=["phb2024.phb05.ability_score_improvement"],
                  note="Черта указана на 4-м, 8-м, 12-м и 16-м уровнях Барда."),
             fact("istochnik_vdohnoveniya", (54, "53", "Источник вдохновения"),
                  consts=dict(recharge=["Короткий отдых", "Долгий отдых"], spell_slot_for_use=True)),
             fact("kontrocharovanie", (54, "53", "Контрочарование"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Проваленный спасбросок против Очарованного или Испуганного — "
                             "что именно резолвится перебросом?"],
                  consts=dict(range_feet=30, reaction=True, new_roll_advantage=True)),
             fact("tainy_magii", (54, "53", "Тайны магии"),
                  consts=dict(from_lists=["Бард", "Волшебник", "Друид", "Жрец"])),
             fact("prevoshodnoe_vdohnovenie", (54, "53", "Превосходное вдохновение"),
                  consts=dict(on_initiative_restore_to=2)),
             fact("epicheskii_dar", (54, "53", "Эпический дар"),
                  deps=["phb2024.phb05.epic_boon"]),
             fact("slova_tvoreniya", (54, "53", "Слова Творения"),
                  consts=dict(second_target_range_feet=10,
                              spells=["Слово силы: исцеление", "Слово силы: смерть"])),
             # Подклассы (3-й уровень): карта фиксирует их как отдельные ветки.
             fact("kolegiya_dobletsi_boevoe_vdohnovenie",
                  (57, "56", "Подкласс «Коллегия доблести»: Боевое вдохновение"),
                  consts=dict(unlock_level=3, effect="Кость вдохновения к КЗ (Реакцией) "
                                                      "или к урону атаки")),
             fact("kolegiya_znanii_ostroe_slovtso",
                  (58, "57", "Подкласс «Коллегия знаний»: Острое словцо"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какое существо и какой бросок затронуты реакцией Острого словца?"],
                  consts=dict(unlock_level=3)),
             fact("kolegiya_ocharovaniya_maniya_vdohnoveniya",
                  (59, "58", "Подкласс «Коллегия очарования»: Мантия вдохновения"),
                  consts=dict(unlock_level=3)),
             fact("kolegiya_tantsa_oslepitelnye_fuete",
                  (60, "59", "Подкласс «Коллегия танца»: Ослепительные фуэте"),
                  consts=dict(unlock_level=3)),
         ]),
    dict(slug="barbarian", file="02-barbarian.json", name="Варвар", pages=(61, 68), start=62,
         genitive="Варвара",
         attributes=dict(hit_die="к12", primary="Сила", saves=["Сила", "Телосложение"],
                         weapons="Простое и Воинское оружие",
                         armor="Лёгкие и Средние доспехи и Щиты"),
         subclass_level=3,
         subclasses=["Путь берсерка", "Путь дикого сердца", "Путь Мирового Древа", "Путь фанатика"],
         resources=["использования Ярости", "Урон от Ярости"],
         features=[
             fact("yarost", (62, "61", "Ярость"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Что подтверждает, что Варвар впал в Ярость и она ещё действует на этом ходу?"],
                  consts=dict(bonus_action=True, no_heavy_armor=True,
                              uses_by_level={"1": 2, "3": 3, "6": 4, "12": 5, "17": 6},
                              recharge="одно использование за Короткий отдых, все за Долгий",
                              resistance=["Дробящий", "Колющий", "Рубящий"],
                              damage_bonus_by_level={"1": 2, "9": 3, "16": 4},
                              strength_advantage=True, no_concentration=True,
                              no_spells=True, max_duration_minutes=10,
                              ends_when=["надет Тяжёлый доспех", "Недееспособность"]),
                  resource="Ярость", resource_id="rage_uses"),
             fact("zashita_bez_dospehov", (62, "61", "Защита без доспехов (Варвар)"),
                  consts=dict(ac_base=10, add_dex=True, add_con=True, shield_allowed=True),
                  deps=["phb2024.phb01.ability_modifier"]),
             fact("iskusnoe_vladenie_oruzhiem", (63, "62", "Искусное владение оружием (Варвар)"),
                  consts=dict(types_at_level_1=2, change_on="Долгий отдых",
                              types_by_level={"1": 2, "4": 3, "10": 4, "16": 5}),
                  deps=["phb2024.phb05.weapon_mastery"]),
             fact("chuvstvo_opasnosti", (63, "62", "Чувство опасности"),
                  consts=dict(dex_saves_advantage=True),
                  deps=["phb2024.phb01.advantage"]),
             fact("bezrassudnaya_ataka", (63, "62", "Безрассудная атака"),
                  consts=dict(trigger="первый бросок атаки в свой ход",
                              advantage_on_strength_attacks=True,
                              attacks_against_advantage=True,
                              until="начало следующего хода")),
             fact("podklass_varvara", (63, "62", "Подкласс Варвара"),
                  consts=dict(subclass_level=3,
                              subclasses=["Путь берсерка", "Путь дикого сердца",
                                          "Путь Мирового Древа", "Путь фанатика"])),
             fact("pervobytnoe_znanie", (63, "62", "Первобытное знание"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какой навык Варвар проверяет Силой под Яростью?"],
                  consts=dict(extra_skill=1,
                              strength_checks=["Акробатика", "Восприятие", "Выживание",
                                              "Запугивание", "Скрытность"],
                              requires_rage=True)),
             fact("uvelichenie_harakteristik", (63, "62", "Увеличение характеристик"),
                  consts=dict(levels=[4, 8, 12, 16]),
                  deps=["phb2024.phb05.ability_score_improvement"]),
             fact("dopolnitelnaya_ataka", (63, "62", "Дополнительная атака"),
                  consts=dict(attacks_per_attack_action=2, unlock_level=5),
                  deps=["phb2024.phb02.attack_action"]),
             fact("bystroe_peredvizhenie", (63, "62", "Быстрое передвижение"),
                  consts=dict(speed_bonus_feet=10, unless_heavy_armor=True)),
             fact("dikii_instinkt", (64, "63", "Дикий инстинкт"),
                  consts=dict(initiative_advantage=True)),
             fact("instinktivnyi_brosok", (64, "63", "Инстинктивный бросок"),
                  consts=dict(move="половина Скорости", part_of="Бонусное действие Ярости")),
             fact("zhestokii_udar", (64, "63", "Жестокий удар"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какой бросок атаки Варвар лишает Преимущества ради Жестокого удара?"],
                  consts=dict(extra_damage="1к10", push_feet=15, slow_feet=15,
                              requires="Безрассудная атака")),
             fact("nepreklonnaya_yarost", (64, "63", "Непреклонная ярость"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какой спасбросок Телосложения Сл 10 совершается при падении до 0 Хитов?"],
                  consts=dict(dc=10, dc_step=5, dc_reset="Короткий или Долгий отдых",
                              hp_on_success="удвоенный уровень Варвара")),
             fact("uluchshennyi_zhestokii_udar_13", (64, "63", "Улучшенный Жестокий удар (13-й уровень)"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какой эффект Жестокого удара применён и к кому?"],
                  consts=dict(dc_penalty_effect=True, split_bonus=5)),
             fact("nepreryvnaya_yarost", (64, "63", "Непрерывная ярость"),
                  consts=dict(restore_on_initiative=True, max_duration_minutes=10)),
             fact("uluchshennyi_zhestokii_udar_17", (64, "63", "Улучшенный Жестокий удар (17-й уровень)"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какие два эффекта Жестокого удара выбраны?"],
                  consts=dict(extra_damage="2к10", effects=2)),
             fact("neukrotimaya_mosh", (64, "63", "Неукротимая мощь"),
                  consts=dict(min_result_equals="значение Силы", applies_to=["проверка", "спасбросок"])),
             fact("epicheskii_dar", (64, "63", "Эпический дар"),
                  deps=["phb2024.phb05.epic_boon"]),
             fact("dikii_chempion", (64, "63", "Дикий чемпион"),
                  consts=dict(strength_bonus=4, constitution_bonus=4, cap=25)),
         ]),
    dict(slug="fighter", file="03-fighter.json", name="Воин", pages=(69, 78), start=70,
         genitive="Воина",
         attributes=dict(hit_die="к10", primary="Сила или Ловкость", saves=["Сила", "Телосложение"],
                         weapons="Простое и Воинское оружие",
                         armor="Лёгкие, Средние и Тяжёлые доспехи и Щиты"),
         subclass_level=3,
         subclasses=["Мастер боя", "Мистический рыцарь", "Пси-воин", "Чемпион"],
         resources=["использования Второго дыхания", "Всплеск действий", "Упорный"],
         features=[
             fact("boevoi_stil", (70, "69", "Боевой стиль (Воин)"),
                  consts=dict(feats=1, change_on="получение уровня Воина"),
                  deps=["phb2024.phb05.fighting_style"]),
             fact("vtoroe_dyhanie", (70, "69", "Второе дыхание"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какое применение Второго дыхания подтверждено — Хиты или переброс проверки?"],
                  consts=dict(heal="1к10 + уровень Воина", bonus_action=True,
                              uses_by_level={"1": 2, "4": 3, "10": 4},
                              recharge="одно за Короткий отдых, все за Долгий"),
                  resource="Второе дыхание", resource_id="second_wind_uses"),
             fact("iskusnoe_vladenie_oruzhiem", (70, "69", "Искусное владение оружием (Воин)"),
                  consts=dict(types_at_level_1=3, change_on="Долгий отдых",
                              types_by_level={"1": 3, "4": 4, "10": 5, "16": 6}),
                  deps=["phb2024.phb05.weapon_mastery"]),
             fact("vsplesk_deistvii", (70, "69", "Всплеск действий"),
                  consts=dict(extra_action=True, forbidden_actions=["Магия"],
                              uses_by_level={"2": 1, "17": 2}, max_per_turn=1,
                              recharge="Короткий или Долгий отдых"),
                  deps=["phb2024.phb02.action_economy"],
                  resource="Всплеск действий", resource_id="action_surge_uses"),
             fact("takticheskoe_myshlenie", (70, "69", "Тактическое мышление"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Провалена ли проверка и потрачено ли Второе дыхание на 1к10?"],
                  consts=dict(die="1к10", uses_second_wind=True)),
             fact("podklass_voina", (71, "70", "Подкласс Воина"),
                  consts=dict(subclass_level=3,
                              subclasses=["Мастер боя", "Мистический рыцарь", "Пси-воин", "Чемпион"])),
             fact("uvelichenie_harakteristik", (71, "70", "Увеличение характеристик"),
                  consts=dict(levels=[4, 6, 8, 12, 14, 16]),
                  deps=["phb2024.phb05.ability_score_improvement"]),
             fact("dopolnitelnaya_ataka", (71, "70", "Дополнительная атака"),
                  consts=dict(attacks_per_attack_action=2, unlock_level=5),
                  deps=["phb2024.phb02.attack_action"]),
             fact("takticheskoe_peremeshenie", (71, "70", "Тактическое перемещение"),
                  consts=dict(move="половина Скорости", no_opportunity_attacks=True)),
             fact("upornyi", (71, "70", "Упорный"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Провален ли спасбросок, который перебрасывается Упорным?"],
                  consts=dict(bonus="уровень Воина", uses_by_level={"9": 1, "13": 2, "17": 3},
                              recharge="Долгий отдых", must_use_new_result=True)),
             fact("master_taktiki", (71, "70", "Мастер тактики"),
                  consts=dict(replacements=["Отталкивающее", "Ослабляющее", "Замедляющее"]),
                  deps=["phb2024.phb05.weapon_mastery"]),
             fact("dve_dopolnitelnye_ataki", (71, "70", "Две дополнительные атаки"),
                  consts=dict(attacks_per_attack_action=3, unlock_level=11),
                  deps=["phb2024.phb02.attack_action"]),
             fact("vyverennye_ataki", (71, "70", "Выверенные атаки"),
                  consts=dict(advantage_after_miss=True, until="конец следующего хода")),
             fact("epicheskii_dar", (71, "70", "Эпический дар"),
                  deps=["phb2024.phb05.epic_boon"]),
             fact("tri_dopolnitelnye_ataki", (71, "70", "Три дополнительные атаки"),
                  consts=dict(attacks_per_attack_action=4, unlock_level=20),
                  deps=["phb2024.phb02.attack_action"]),
         ]),
    dict(slug="wizard", file="04-wizard.json", name="Волшебник", pages=(79, 90), start=80,
         genitive="Волшебника",
         attributes=dict(hit_die="к6", primary="Интеллект", saves=["Интеллект", "Мудрость"],
                         weapons="Простое оружие", armor="Отсутствует"),
         subclass_level=3,
         subclasses=["Воплотитель", "Иллюзионист", "Оградитель", "Прорицатель"],
         resources=["ячейки заклинаний", "книга заклинаний", "Магическое восстановление"],
         features=[
             fact("sotvorenie_zaklinanii", (80, "79", "Сотворение заклинаний (Волшебник)"),
                  consts=dict(cantrips_start=3, cantrip_levels=[4, 10], spellbook_spells_start=6,
                              spellbook_add_per_level=2, prepared_level_1=4,
                              spellcasting_ability="Интеллект", book_weight_lb=3, book_pages=100),
                  deps=["phb2024.phb07.spellbook"]),
             fact("znatok_ritualov", (81, "80", "Знаток ритуалов"),
                  consts=dict(ritual_only_from_book=True, no_preparation_needed=True)),
             fact("magicheskoe_vosstanovlenie", (81, "80", "Магическое восстановление"),
                  consts=dict(recharge="Короткий отдых", max_slot_level_total="половина уровня (в большую сторону)",
                              forbidden_slot_level=6, max_levels="floor(уровень/2)",
                              example_level_4_levels=2, once_per_long_rest=True),
                  deps=["phb2024.phb01.rounding"]),
             fact("akademicheskie_znaniya", (81, "80", "Академические знания"),
                  consts=dict(skill_choices=["История", "Медицина", "Природа", "Расследование",
                                             "Религия", "Тайная магия"], expertise=True)),
             fact("podklass_volshebnika", (82, "81", "Подкласс Волшебника"),
                  consts=dict(subclass_level=3,
                              subclasses=["Воплотитель", "Иллюзионист", "Оградитель", "Прорицатель"])),
             fact("uvelichenie_harakteristik", (82, "81", "Увеличение характеристик"),
                  consts=dict(levels=[4, 8, 12, 16]),
                  deps=["phb2024.phb05.ability_score_improvement"]),
             fact("zapominanie_zaklinaniya", (82, "81", "Запоминание заклинания"),
                  consts=dict(recharge="Короткий отдых", replacements=1)),
             fact("masterstvo_zaklinatelya", (82, "81", "Мастерство заклинателя"),
                  consts=dict(free_spells=[1, 2], cast_time="1 действие", free_at_lowest_level=True,
                              replace_on="Долгий отдых")),
             fact("epicheskii_dar", (82, "81", "Эпический дар"),
                  deps=["phb2024.phb05.epic_boon"]),
             fact("firmennye_zaklinaniya", (82, "81", "Фирменные заклинания"),
                  consts=dict(spells=2, spell_level=3, free_casts=1,
                              recharge="Короткий или Долгий отдых")),
             fact("kopirovanie_zaklinanii", (82, "81", "Копирование заклинания в книгу"),
                  consts=dict(hours_per_level_new=2, gold_per_level_new=50,
                              hours_per_level_copy=1, gold_per_level_copy=10),
                  deps=["phb2024.phb07.spellbook"]),
         ]),
    dict(slug="druid", file="05-druid.json", name="Друид", pages=(91, 102), start=92,
         genitive="Друида",
         attributes=dict(hit_die="к8", primary="Мудрость", saves=["Интеллект", "Мудрость"],
                         weapons="Простое оружие", tools="Набор травника",
                         armor="Лёгкие доспехи и Щиты"),
         subclass_level=3,
         subclasses=["Круг звёзд", "Круг земли", "Круг луны", "Круг моря"],
         resources=["использования Дикого облика", "ячейки заклинаний"],
         features=[
             fact("sotvorenie_zaklinanii", (92, "91", "Сотворение заклинаний (Друид)"),
                  consts=dict(cantrips_start=2, cantrip_levels=[4, 10], prepared_level_1=4,
                              spellcasting_ability="Мудрость",
                              replace_prepared="любое количество на Долгом отдыхе"),
                  deps=["phb2024.phb01.spell_slots"]),
             fact("druidicheskii_yazyk", (93, "92", "Друидический язык"),
                  consts=dict(always_prepared=["Разговор с животными"],
                              investigation_dc=15, ability="Интеллект")),
             fact("pervozdannyi_orden", (93, "92", "Первозданный орден"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какой орден выбран — Волхв или Смотритель?"],
                  consts=dict(cantrip_bonus=1, bonus_skills=["Тайная магия", "Природа"],
                              bonus_source="модификатор Мудрости, минимум +1",
                              guardian_armor="Средние доспехи", guardian_weapons="Воинское оружие"),
                  deps=["phb2024.phb01.ability_modifier"]),
             fact("dikii_oblik", (93, "92", "Дикий облик"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["В какого известного Зверя перевоплощение подтверждено и есть ли "
                             "подходящий блок статистики?"],
                  consts=dict(bonus_action=True, hours="половина уровня Друида",
                              uses_start=2, recharge="одно за Короткий отдых, все за Долгий",
                              known_shapes_start=4, max_cr_start="1/4",
                              no_fly_speed_until_level=8,
                              temp_hp="равно уровню Друида",
                              shapes_by_level={"2": dict(known=4, max_cr="1/4", fly=False),
                                               "4": dict(known=6, max_cr="1/2", fly=False),
                                               "8": dict(known=8, max_cr="1", fly=True)},
                              keep=["тип существа", "Хиты", "Кости хитов", "Интеллект",
                                    "Мудрость", "Харизма", "классовые умения", "языки", "черты"]),
                  deps=["mm_2025_ru_v1_1.beast_blocks"],
                  resource="Дикий облик", resource_id="wild_shape_uses"),
             fact("dikii_sputnik", (94, "93", "Дикий спутник"),
                  consts=dict(action="Магия", costs=["ячейка заклинаний", "использование Дикого облика"],
                              spell="Обретение фамильяра", no_material=True,
                              familiar_type="Фея", expires="после Долгого отдыха")),
             fact("podklass_druida", (94, "93", "Подкласс Друида"),
                  consts=dict(subclass_level=3,
                              subclasses=["Круг звёзд", "Круг земли", "Круг луны", "Круг моря"])),
             fact("uvelichenie_harakteristik", (94, "93", "Увеличение характеристик"),
                  consts=dict(levels=[4, 8, 12, 16]),
                  deps=["phb2024.phb05.ability_score_improvement"]),
             fact("dikoe_vosstanovlenie", (94, "93", "Дикое восстановление"),
                  consts=dict(slot_to_use=True, use_to_slot_level=1, once_per_long_rest=True)),
             fact("yarost_stihii", (94, "93", "Ярость стихий"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какой вариант Ярости стихий выбран — Могущественное колдовство "
                             "или Удар природы?"],
                  consts=dict(nature_strike_extra_damage="1к8",
                              damage_types=["Звук", "Огонь", "Холод", "Электричество"],
                              spell_damage_bonus="модификатор Мудрости")),
             fact("uluchshennaya_yarost_stihii", (95, "94", "Улучшенная ярость стихий"),
                  consts=dict(range_bonus_feet=300, nature_strike_extra_damage="2к8")),
             fact("zaklinaniya_zverya", (95, "94", "Заклинания зверя"),
                  consts=dict(forbidden="Материальный компонент, расходуемый заклинанием, "
                                        "или указанная цена")),
             fact("epicheskii_dar", (95, "94", "Эпический дар"),
                  deps=["phb2024.phb05.epic_boon"]),
             fact("arhidruid", (95, "94", "Архидруид"),
                  consts=dict(restore_one_use_on_initiative=True,
                              use_to_slot_level_step=2, once_per_long_rest=True,
                              aging="1 год за каждые 10 лет")),
         ]),
    dict(slug="cleric", file="06-cleric.json", name="Жрец", pages=(103, 112), start=104,
         genitive="Жреца",
         attributes=dict(hit_die="к8", primary="Мудрость", saves=["Мудрость", "Харизма"],
                         weapons="Простое оружие", armor="Лёгкие и Средние доспехи и Щиты"),
         subclass_level=3,
         subclasses=["Домен войны", "Домен жизни", "Домен света", "Домен обмана"],
         resources=["использования Проведения божественности", "ячейки заклинаний"],
         features=[
             fact("sotvorenie_zaklinanii", (104, "103", "Сотворение заклинаний (Жрец)"),
                  consts=dict(cantrips_start=3, cantrip_levels=[4, 10], prepared_level_1=4,
                              spellcasting_ability="Мудрость",
                              replace_prepared="любое количество на Долгом отдыхе"),
                  deps=["phb2024.phb01.spell_slots"]),
             fact("bozhestvennyi_orden", (105, "104", "Божественный орден"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какой орден выбран — Защитник или Чудотворец?"],
                  consts=dict(cantrip_bonus=1, bonus_skills=["Тайная магия", "Религия"],
                              bonus_source="модификатор Мудрости, минимум +1",
                              protector_armor="Тяжёлые доспехи", protector_weapons="Воинское оружие"),
                  deps=["phb2024.phb01.ability_modifier"]),
             fact("provedenie_bozhestvennosti", (105, "104", "Проведение божественности (Жрец)"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какой эффект Проведения божественности создаётся?"],
                  consts=dict(effects_start=["Божественная искра", "Изгнание нежити"],
                              uses_by_level={"2": 2, "6": 3, "18": 4},
                              recharge="одно за Короткий отдых, все за Долгий",
                              dc="Сл спасброска заклинаний Жреца"),
                  resource="Проведение божественности", resource_id="channel_divinity_uses"),
             fact("bozhestvennaya_iskra", (106, "105", "Божественная искра"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Исцеление или урон, и какой тип урона выбирает Жрец?"],
                  consts=dict(range_feet=30, action="Магия", die="1к8",
                              die_by_level={"1": "1к8", "7": "2к8", "13": "3к8", "18": "4к8"},
                              add="модификатор Мудрости", save="Телосложение",
                              damage_types=["Некротическая энергия", "Излучение"],
                              half_on_success="floor"),
                  deps=["phb2024.phb01.half_damage"]),
             fact("izgnanie_nezheti", (106, "105", "Изгнание нежити"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какие именно нежить в пределах 30 футов попадают под Изгнание?"],
                  consts=dict(range_feet=30, action="Магия", save="Мудрость",
                              conditions=["Испуганный", "Недееспособный"], duration_minutes=1,
                              flee_each_turn=True)),
             fact("podklass_zheretsa", (106, "105", "Подкласс Жреца"),
                  consts=dict(subclass_level=3,
                              subclasses=["Домен войны", "Домен жизни", "Домен света", "Домен обмана"])),
             fact("uvelichenie_harakteristik", (106, "105", "Увеличение характеристик"),
                  consts=dict(levels=[4, 8, 12, 16]),
                  deps=["phb2024.phb05.ability_score_improvement"]),
             fact("ispeplenie_nezheti", (106, "105", "Испепеление нежити"),
                  consts=dict(dice="к8 в количестве модификатора Мудрости, минимум 1",
                              damage_type="Излучение", ends_effect=False)),
             fact("blagoslovlennye_udary", (106, "105", "Благословлённые удары"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какой вариант Благословлённых ударов выбран?"],
                  consts=dict(divine_strike_die="1к8", damage_types=["Излучение", "Некротическая энергия"],
                              spell_damage_bonus="модификатор Мудрости", once_per_turn=True)),
             fact("bozhestvennoe_vmeshatelstvo", (106, "105", "Божественное вмешательство"),
                  consts=dict(spell_level_max=5, no_reaction_spells=True, no_slot=True,
                              once_per_long_rest=True)),
             fact("uluchshennye_blagoslovlennye_udary", (106, "105", "Улучшенные Благословлённые удары"),
                  consts=dict(divine_strike_die="2к8",
                              temp_hp="удвоенный модификатор Мудрости", range_feet=60)),
             fact("epicheskii_dar", (106, "105", "Эпический дар"),
                  deps=["phb2024.phb05.epic_boon"]),
             fact("velikoe_bozhestvennoe_vmeshatelstvo", (106, "105", "Великое божественное вмешательство"),
                  consts=dict(spell="Желание", recharge="2к4 Долгих отдыха")),
         ]),
    dict(slug="warlock", file="07-warlock.json", name="Колдун", pages=(113, 124), start=114,
         genitive="Колдуна",
         attributes=dict(hit_die="к8", primary="Харизма", saves=["Мудрость", "Харизма"],
                         weapons="Простое оружие", armor="Лёгкие доспехи"),
         subclass_level=3,
         subclasses=["Покровительство Архифеи", "Покровительство Великого Древнего",
                     "Покровительство Исчадия", "Покровительство Небожителя"],
         resources=["ячейки Магии договора (все одного уровня)", "Таинственные воззвания",
                    "Таинственный арканум"],
         features=[
             fact("tainstvennye_vozzvaniya", (114, "113", "Таинственные воззвания"),
                  consts=dict(start=1,
                              by_level={"1": 1, "2": 3, "5": 5, "7": 6, "9": 7,
                                        "12": 8, "15": 9, "18": 10},
                              replace_on="получение уровня Колдуна"),
                  resource="Таинственные воззвания", resource_id="eldritch_invocations_known"),
             fact("magiya_dogovora", (114, "113", "Магия договора"),
                  consts=dict(cantrips_start=2, cantrip_levels=[4, 10], prepared_level_1=2,
                              spellcasting_ability="Харизма",
                              slots_all_same_level=True, max_slot_level=5,
                              slot_table={"1": [1, 1], "2": [2, 1], "3": [2, 2], "4": [2, 2],
                                          "5": [2, 3], "6": [2, 3], "7": [2, 4], "8": [2, 4],
                                          "9": [2, 5], "10": [2, 5], "11": [3, 5], "12": [3, 5],
                                          "13": [3, 5], "14": [3, 5], "15": [3, 5], "16": [3, 5],
                                          "17": [4, 5], "18": [4, 5], "19": [4, 5], "20": [4, 5]},
                              recharge="Короткий или Долгий отдых"),
                  deps=["phb2024.phb01.spell_slots"]),
             fact("magicheskaya_izvorotlivost", (115, "114", "Магическая изворотливость"),
                  consts=dict(ritual_minutes=1, restore="половина максимума, округляя в большую сторону",
                              once_per_long_rest=True),
                  deps=["phb2024.phb01.rounding"]),
             fact("podklass_kolduna", (116, "115", "Подкласс Колдуна"),
                  consts=dict(subclass_level=3,
                              subclasses=["Покровительство Архифеи", "Покровительство Великого Древнего",
                                          "Покровительство Исчадия", "Покровительство Небожителя"])),
             fact("uvelichenie_harakteristik", (116, "115", "Увеличение характеристик"),
                  consts=dict(levels=[4, 8, 12, 16]),
                  deps=["phb2024.phb05.ability_score_improvement"]),
             fact("svyaz_s_pokrovitelem", (116, "115", "Связь с покровителем"),
                  consts=dict(always_prepared=["Связь с иным планом"], no_slot=True,
                              auto_success_save=True, once_per_long_rest=True)),
             fact("tainstvennyi_arkanum", (116, "115", "Таинственный арканум"),
                  consts=dict(levels={"11": 6, "13": 7, "15": 8, "17": 9}, free_cast=True,
                              recharge="Долгий отдых", replace_on="получение уровня Колдуна")),
             fact("epicheskii_dar", (116, "115", "Эпический дар"),
                  deps=["phb2024.phb05.epic_boon"]),
             fact("tainstvennyi_master", (116, "115", "Таинственный мастер"),
                  consts=dict(restore_all_slots=True)),
             fact("vedmachii_vzor", (116, "115", "Ведьмачий взор"),
                  consts=dict(truesight_feet=30, requires_level=15)),
             fact("vzor_dvuh_umov", (116, "115", "Взор двух умов"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какое согласное существо касается Колдун и что оно воспринимает?"],
                  consts=dict(requires_level=5, touch=True, bonus_action=True,
                              spell_range_feet=60)),
             fact("dar_glubin", (116, "115", "Дар глубин"),
                  consts=dict(requires_level=5, swim_speed_equals_speed=True,
                              spell="Подводное дыхание", free_casts=1,
                              recharge="Долгий отдых")),
         ]),
    dict(slug="monk", file="08-monk.json", name="Монах", pages=(125, 132), start=126,
         genitive="Монаха",
         attributes=dict(hit_die="к8", primary="Ловкость и Мудрость", saves=["Сила", "Ловкость"],
                         weapons="Простое оружие и Воинское оружие со свойством Лёгкое",
                         tools="Ремесленные инструменты или Музыкальный инструмент",
                         armor="Отсутствует"),
         subclass_level=3,
         subclasses=["Воин милосердия", "Воин открытой ладони", "Воин стихий", "Воин тени"],
         resources=["Очки сосредоточенности", "Кость боевых искусств"],
         features=[
             fact("boevye_iskusstva", (126, "125", "Боевые искусства"),
                  consts=dict(martial_arts_die_by_level={"1": "к6", "5": "к8", "11": "к10", "17": "к12"},
                              bonus_unarmed_strike=True, dex_instead_of_strength=True,
                              dex_for_grapple_dc=True, no_armor_no_shield=True),
                  deps=["phb2024.phb02.unarmed_strike"]),
             fact("zashita_bez_dospehov", (126, "125", "Защита без доспехов (Монах)"),
                  consts=dict(ac_base=10, add_dex=True, add_wis=True, no_armor_or_shield=True),
                  deps=["phb2024.phb01.ability_modifier"]),
             fact("monasheskaya_sosredotochennost", (126, "125", "Монашеская сосредоточенность"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какое умение за Очко сосредоточенности применяется?"],
                  consts=dict(points_by_level={"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
                                               "8": 8, "9": 9, "10": 10, "11": 11, "12": 12,
                                               "13": 13, "14": 14, "15": 15, "16": 16, "17": 17,
                                               "18": 18, "19": 19, "20": 20},
                              options_start=["Шквал ударов", "Терпеливая оборона", "Поступь ветра"],
                              dc="8 + модификатор Мудрости + Бонус владения",
                              recharge="Короткий или Долгий отдых"),
                  resource="Очки сосредоточенности", resource_id="focus_points"),
             fact("shkval_udarov", (127, "126", "Шквал ударов"),
                  consts=dict(cost=1, bonus_action=True, unarmed_strikes=2),
                  resource="Очки сосредоточенности", resource_id="focus_points"),
             fact("terpelivaya_oborona", (127, "126", "Терпеливая оборона"),
                  consts=dict(cost=1, bonus_action=True, actions=["Отход", "Уклонение"]),
                  resource="Очки сосредоточенности", resource_id="focus_points"),
             fact("postup_vetra", (127, "126", "Поступь ветра"),
                  consts=dict(cost=1, bonus_action=True, actions=["Отход", "Рывок"],
                              jump_distance_doubled=True),
                  resource="Очки сосредоточенности", resource_id="focus_points"),
             fact("dvizhenie_bez_dospehov", (127, "126", "Движение без доспехов"),
                  consts=dict(bonus_by_level={"2": 10, "6": 15, "10": 20, "14": 25, "18": 30},
                              unless_armor_or_shield=True)),
             fact("fenomenalnyi_metabolizm", (127, "126", "Феноменальный метаболизм"),
                  consts=dict(on_initiative=True, restore_all_focus=True,
                              heal="уровень Монаха + Кость боевых искусств",
                              once_per_long_rest=True)),
             fact("otrazhenie_atak", (127, "126", "Отражение атак"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какая атака по Монаху уменьшается и кому перенаправляется урон?"],
                  consts=dict(reduction="1к10 + модификатор Ловкости + уровень Монаха",
                              redirect_cost=1, redirect_range_feet=[5, 60],
                              redirect_damage="две Кости боевых искусств + модификатор Ловкости",
                              damage_types=["Дробящий", "Колющий", "Рубящий"])),
             fact("podklass_monaha", (127, "126", "Подкласс Монаха"),
                  consts=dict(subclass_level=3,
                              subclasses=["Воин милосердия", "Воин открытой ладони",
                                          "Воин стихий", "Воин тени"])),
             fact("uvelichenie_harakteristik", (128, "127", "Увеличение характеристик"),
                  consts=dict(levels=[4, 8, 12, 16]),
                  deps=["phb2024.phb05.ability_score_improvement"]),
             fact("medlennoe_padenie", (128, "127", "Медленное падение"),
                  consts=dict(reduction="пятикратный уровень Монаха", reaction=True)),
             fact("dopolnitelnaya_ataka", (128, "127", "Дополнительная атака"),
                  consts=dict(attacks_per_attack_action=2, unlock_level=5),
                  deps=["phb2024.phb02.attack_action"]),
             fact("oshelomlyayushii_udar", (128, "127", "Ошеломляющий удар"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какая цель и какой бросок попадания под Ошеломляющий удар?"],
                  consts=dict(cost=1, once_per_turn=True, save="Телосложение",
                              fail_condition="Ошеломлённый до начала следующего хода",
                              success="скорость вдвое, следующий бросок атаки по цели с Преимуществом"),
                  resource="Очки сосредоточенности", resource_id="focus_points"),
             fact("usilennye_udary", (128, "127", "Усиленные удары"),
                  consts=dict(damage_type_choice=["обычный тип", "Чистая сила"])),
             fact("uvertlivost", (128, "127", "Увёртливость (Монах)"),
                  deps=["phb2024.phb02.dexterity_save_half_damage"]),
             fact("akrobatichnoe_peremeshenie", (128, "127", "Акробатичное перемещение"),
                  consts=dict(vertical_surfaces=True, liquids=True, no_armor_or_shield=True)),
             fact("povyshennaya_sosredotochennost", (128, "127", "Повышенная сосредоточенность"),
                  consts=dict(flurry_strikes=3,
                              patient_defense_temp_hp="две Кости боевых искусств",
                              step_of_wind_companion_size_max="Большой", companion_range_feet=5)),
             fact("samovosstanovlenie", (128, "127", "Самовосстановление"),
                  consts=dict(conditions=["Испуганный", "Отравленный", "Очарованный"],
                              no_food_water_exhaustion=True)),
             fact("otrazhenie_energii", (128, "127", "Отражение энергий"),
                  consts=dict(any_damage_type=True)),
             fact("obuchennyi_vyzhivat", (128, "127", "Обученный выживать"),
                  consts=dict(all_saves_proficiency=True, reroll_cost=1, must_use_new_result=True)),
             fact("polnaya_sosredotochennost", (128, "127", "Полная сосредоточенность"),
                  consts=dict(on_initiative_restore_to=4, threshold=3)),
             fact("sovershennaya_zashita", (128, "127", "Совершенная защита"),
                  consts=dict(cost=3, duration_minutes=1, resistance="весь урон, кроме Чистой силы")),
             fact("epicheskii_dar", (128, "127", "Эпический дар"),
                  deps=["phb2024.phb05.epic_boon"]),
             fact("telo_i_razum", (128, "127", "Тело и разум"),
                  consts=dict(dexterity_bonus=4, wisdom_bonus=4, cap=25)),
         ]),
    dict(slug="paladin", file="09-paladin.json", name="Паладин", pages=(133, 142), start=134,
         genitive="Паладина",
         attributes=dict(hit_die="к10", primary="Сила и Харизма", saves=["Мудрость", "Харизма"],
                         weapons="Простое и Воинское оружие",
                         armor="Лёгкие, Средние и Тяжёлые доспехи и Щиты"),
         subclass_level=3,
         subclasses=["Клятва возмездия", "Клятва древних", "Клятва преданности", "Клятва славы"],
         resources=["запас Возложения рук", "ячейки заклинаний", "Проведение божественности"],
         features=[
             fact("vozlozhenie_ruk", (134, "133", "Возложение рук"),
                  consts=dict(pool="пятикратный уровень Паладина", bonus_action=True,
                              recharge="Долгий отдых", poison_cure_cost=5),
                  resource="Возложение рук", resource_id="lay_on_hands_pool"),
             fact("sotvorenie_zaklinanii", (134, "133", "Сотворение заклинаний (Паладин)"),
                  consts=dict(prepared_level_1=2, spellcasting_ability="Харизма",
                              prepared_by_level={"1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 6,
                                                 "7": 7, "8": 7, "9": 9, "10": 9, "11": 10,
                                                 "12": 10, "13": 11, "14": 11, "15": 12, "16": 12,
                                                 "17": 14, "18": 14, "19": 15, "20": 15},
                              replace_prepared=1, recharge="Долгий отдых"),
                  deps=["phb2024.phb01.spell_slots"]),
             fact("iskusnoe_vladenie_oruzhiem", (135, "134", "Искусное владение оружием (Паладин)"),
                  consts=dict(types=2, change_on="Долгий отдых"),
                  deps=["phb2024.phb05.weapon_mastery"]),
             fact("boevoi_stil", (135, "134", "Боевой стиль (Паладин)"),
                  consts=dict(feats=1, blessed_warrior_cantrips=2,
                              blessed_warrior_list="Жрец", ability="Харизма"),
                  deps=["phb2024.phb05.fighting_style"]),
             fact("kara_paladina", (135, "134", "Кара Паладина"),
                  consts=dict(always_prepared=["Божественная кара"], free_cast=1,
                              recharge="Долгий отдых")),
             fact("provedenie_bozhestvennosti", (135, "134", "Проведение божественности (Паладин)"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какой эффект Проведения божественности создаётся Паладином?"],
                  consts=dict(effects_start=["Божественное чувство"],
                              uses_by_level={"3": 2, "11": 3},
                              recharge="одно за Короткий отдых, все за Долгий",
                              dc="Сл спасброска заклинаний Паладина"),
                  resource="Проведение божественности", resource_id="channel_divinity_uses"),
             fact("bozhestvennoe_chuvstvo", (135, "134", "Божественное чувство"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какие именно Исчадия, Небожители и Нежить обнаружены?"],
                  consts=dict(bonus_action=True, range_feet=60, duration_minutes=10,
                              creature_types=["Исчадие", "Небожитель", "Нежить"],
                              detects_holy_unholy=True)),
             fact("podklass_paladina", (136, "135", "Подкласс Паладина"),
                  consts=dict(subclass_level=3,
                              subclasses=["Клятва возмездия", "Клятва древних",
                                          "Клятва преданности", "Клятва славы"])),
             fact("uvelichenie_harakteristik", (136, "135", "Увеличение характеристик"),
                  consts=dict(levels=[4, 8, 12, 16]),
                  deps=["phb2024.phb05.ability_score_improvement"]),
             fact("dopolnitelnaya_ataka", (136, "135", "Дополнительная атака"),
                  consts=dict(attacks_per_attack_action=2, unlock_level=5),
                  deps=["phb2024.phb02.attack_action"]),
             fact("vernyi_skakun", (136, "135", "Верный скакун"),
                  consts=dict(always_prepared=["Обретение скакуна"], free_cast=1,
                              recharge="Долгий отдых")),
             fact("aura_zashity", (136, "135", "Аура защиты"),
                  consts=dict(radius_feet=10, save_bonus="модификатор Харизмы, минимум +1",
                              single_aura_only=True)),
             fact("poritsanie_vragov", (136, "135", "Порицание врагов"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какие видимые существа в пределах 60 футов выбраны целью Порицания?"],
                  consts=dict(range_feet=60, action="Магия", targets="модификатор Харизмы, минимум 1",
                              save="Мудрость", condition="Испуганный", duration_minutes=1,
                              ends_on_damage=True, uses_conduct_cost=1)),
             fact("aura_otvagi", (136, "135", "Аура отваги"),
                  consts=dict(immunity="Испуганный", within_aura_of_protection=True)),
             fact("siyayushie_udary", (136, "135", "Сияющие удары"),
                  consts=dict(extra_damage="1к8", damage_type="Излучение",
                              applies_to=["Рукопашное оружие", "Безоружный удар"])),
             fact("vosstanavlivayushee_prikosnovenie", (136, "135", "Восстанавливающее прикосновение"),
                  consts=dict(cost_per_condition=5,
                              conditions=["Испуганный", "Оглохший", "Ослеплённый",
                                          "Очарованный", "Ошеломлённый", "Парализованный"])),
             fact("rasshirenie_aury", (136, "135", "Расширение ауры"),
                  consts=dict(radius_feet=30)),
             fact("epicheskii_dar", (136, "135", "Эпический дар"),
                  deps=["phb2024.phb05.epic_boon"]),
         ]),
    dict(slug="rogue", file="10-rogue.json", name="Плут", pages=(143, 152), start=144,
         genitive="Плута",
         attributes=dict(hit_die="к8", primary="Ловкость", saves=["Ловкость", "Интеллект"],
                         weapons="Простое оружие и Воинское оружие со свойством Фехтовальное или Лёгкое",
                         tools="Воровские инструменты", armor="Лёгкие доспехи"),
         subclass_level=3,
         subclasses=["Вор", "Клинок душ", "Мистический ловкач", "Убийца"],
         resources=["Коварная атака", "Хитрый удар"],
         features=[
             fact("ekspertnost", (144, "143", "Экспертность (Плут)"),
                  consts=dict(skills=2, extra_at_level=6),
                  deps=["phb2024.phb01.expertise"]),
             fact("kovarnaya_ataka", (144, "143", "Коварная атака"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Выполнено ли условие Коварной атаки — Преимущество или союзник "
                             "в 5 футах без Помехи?"],
                  consts=dict(dice_by_level={"1": "1к6", "3": "2к6", "5": "3к6", "7": "4к6",
                                             "9": "5к6", "11": "6к6", "13": "7к6", "15": "8к6",
                                             "17": "9к6", "19": "10к6"},
                              once_per_turn=True, ally_range_feet=5,
                              weapons=["Дальнобойное", "Фехтовальное"]),
                  deps=["phb2024.phb02.unarmed_strike"]),
             fact("vorovskoi_zhargon", (144, "143", "Воровской жаргон"),
                  consts=dict(extra_languages=1)),
             fact("iskusnoe_vladenie_oruzhiem", (144, "143", "Искусное владение оружием (Плут)"),
                  consts=dict(types=2, change_on="Долгий отдых"),
                  deps=["phb2024.phb05.weapon_mastery"]),
             fact("hitroe_deistvie", (145, "144", "Хитрое действие"),
                  consts=dict(bonus_action=True, actions=["Засада", "Отход", "Рывок"])),
             fact("podklass_pluta", (145, "144", "Подкласс Плута"),
                  consts=dict(subclass_level=3,
                              subclasses=["Вор", "Клинок душ", "Мистический ловкач", "Убийца"])),
             fact("tochnoe_pritselivanie", (145, "144", "Точное прицеливание"),
                  consts=dict(bonus_action=True, advantage_next_attack=True,
                              requires_no_move=True, speed_zero_after=True)),
             fact("uvelichenie_harakteristik", (145, "144", "Увеличение характеристик"),
                  consts=dict(levels=[4, 8, 10, 12, 16]),
                  deps=["phb2024.phb05.ability_score_improvement"]),
             fact("hitryi_udar", (145, "144", "Хитрый удар"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какой эффект Хитрого удара применён и какие кости за него убраны?"],
                  consts=dict(dc="8 + модификатор Ловкости + Бонус владения",
                              effects={"Отравление": "1к6", "Подсечка": "1к6", "Отступление": "1к6"},
                              poison_tools_required=["Отравление"],
                              target_size_max=["Подсечка", "Большой"]),
                  resource="Коварная атака", resource_id="sneak_attack_dice"),
             fact("neveroyatnoe_uklonenie", (146, "145", "Невероятное уклонение"),
                  consts=dict(reaction=True, damage_halved=True, rounding="floor"),
                  deps=["phb2024.phb01.half_damage"]),
             fact("uvertlivost", (146, "145", "Увёртливость (Плут)"),
                  deps=["phb2024.phb02.dexterity_save_half_damage"]),
             fact("nadezhnyi_talant", (146, "145", "Надёжный талант"),
                  consts=dict(threshold=9, replaced_by=10)),
             fact("uluchshennyi_hitryi_udar", (146, "145", "Улучшенный Хитрый удар"),
                  consts=dict(effects_max=2)),
             fact("beschestnye_udary", (146, "145", "Бесчестные удары"),
                  mode="hybrid", ops=("registered_procedure",),
                  questions=["Какой из новых эффектов Хитрого удара применён?"],
                  consts=dict(effects={"Шок": "2к6", "Нокаут": "6к6", "Ослепление": "3к6"})),
             fact("skolzskii_um", (146, "145", "Скользкий ум"),
                  consts=dict(save_proficiencies=["Мудрость", "Харизма"])),
             fact("neulovimost", (146, "145", "Неуловимость"),
                  consts=dict(no_advantage_against=True)),
             fact("epicheskii_dar", (146, "145", "Эпический дар"),
                  deps=["phb2024.phb05.epic_boon"]),
             fact("ulybka_fortuny", (146, "145", "Улыбка фортуны"),
                  consts=dict(replace_result_with=20, recharge="Короткий или Долгий отдых")),
         ]),
    dict(slug="ranger", file="11-ranger.json", name="Следопыт", pages=(153, 162), start=154,
         genitive="Следопыта",
         attributes=dict(hit_die="к10", primary="Ловкость и Мудрость", saves=["Сила", "Ловкость"],
                         weapons="Простое и Воинское оружие",
                         armor="Лёгкие и Средние доспехи и Щиты"),
         subclass_level=3,
         subclasses=["Охотник", "Повелитель зверей", "Странник фей", "Сумрачный охотник"],
         resources=["использования Избранного врага", "ячейки заклинаний"],
         features=[
             fact("sotvorenie_zaklinanii", (154, "153", "Сотворение заклинаний (Следопыт)"),
                  consts=dict(prepared_level_1=2, spellcasting_ability="Мудрость",
                              prepared_by_level={"1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 6,
                                                 "7": 7, "8": 7, "9": 9, "10": 9, "11": 10,
                                                 "12": 10, "13": 11, "14": 11, "15": 12, "16": 12,
                                                 "17": 14, "18": 14, "19": 15, "20": 15},
                              replace_prepared=1, focus="Друидическая фокусировка"),
                  deps=["phb2024.phb01.spell_slots"]),
             fact("izbrannyi_vrag", (155, "154", "Избранный враг"),
                  consts=dict(always_prepared=["Метка охотника"], free_casts_start=2,
                              free_casts_by_level={"1": 2, "5": 3, "9": 4, "13": 5, "17": 6},
                              recharge="Долгий отдых")),
             fact("iskusnoe_vladenie_oruzhiem", (155, "154", "Искусное владение оружием (Следопыт)"),
                  consts=dict(types=2, change_on="Долгий отдых"),
                  deps=["phb2024.phb05.weapon_mastery"]),
             fact("iskusnyi_issledovatel", (155, "154", "Искусный исследователь"),
                  consts=dict(expertise_skills=1, languages=2)),
             fact("boevoi_stil", (155, "154", "Боевой стиль (Следопыт)"),
                  consts=dict(feats=1, druidic_warrior_cantrips=2, ability="Мудрость",
                              druidic_warrior_list="Друид"),
                  deps=["phb2024.phb05.fighting_style"]),
             fact("podklass_sledopyta", (155, "154", "Подкласс Следопыта"),
                  consts=dict(subclass_level=3,
                              subclasses=["Охотник", "Повелитель зверей", "Странник фей",
                                          "Сумрачный охотник"])),
             fact("uvelichenie_harakteristik", (155, "154", "Увеличение характеристик"),
                  consts=dict(levels=[4, 8, 12, 16]),
                  deps=["phb2024.phb05.ability_score_improvement"]),
             fact("dopolnitelnaya_ataka", (156, "155", "Дополнительная атака"),
                  consts=dict(attacks_per_attack_action=2, unlock_level=5),
                  deps=["phb2024.phb02.attack_action"]),
             fact("vseprolaznost", (156, "155", "Всепролазность"),
                  consts=dict(speed_bonus_feet=10, climb_speed_equals_speed=True,
                              swim_speed_equals_speed=True, unless_heavy_armor=True)),
             fact("ekspertnost", (156, "155", "Экспертность (Следопыт)"),
                  consts=dict(skills=2),
                  deps=["phb2024.phb01.expertise"]),
             fact("neutomimyi", (156, "155", "Неутомимый"),
                  consts=dict(temp_hp="1к8 + модификатор Мудрости, минимум 1",
                              uses="модификатор Мудрости, минимум 1",
                              recharge="Долгий отдых", exhaustion_reduction_per_short_rest=1),
                  resource="Неутомимый", resource_id="tireless_temp_hp"),
             fact("upornyi_ohotnik", (156, "155", "Упорный охотник"),
                  consts=dict(concentration_not_broken_by_damage=True)),
             fact("prirodnaya_zavesa", (156, "155", "Природная завеса"),
                  consts=dict(bonus_action=True, invisible_until_end_of_next_turn=True,
                              uses="модификатор Мудрости, минимум 1",
                              recharge="Долгий отдых")),
             fact("metkii_ohotnik", (156, "155", "Меткий охотник"),
                  consts=dict(advantage_vs_hunters_mark_target=True)),
             fact("dikie_chuvstva", (156, "155", "Дикие чувства"),
                  consts=dict(blindsight_feet=30)),
             fact("epicheskii_dar", (156, "155", "Эпический дар"),
                  deps=["phb2024.phb05.epic_boon"]),
             fact("groza_vragov", (156, "155", "Гроза врагов"),
                  consts=dict(hunters_mark_die="к10")),
         ]),
    dict(slug="sorcerer", file="12-sorcerer.json", name="Чародей", pages=(163, 177), start=164,
         genitive="Чародея",
         attributes=dict(hit_die="к6", primary="Харизма", saves=["Телосложение", "Харизма"],
                         weapons="Простое оружие", armor="Отсутствует"),
         subclass_level=3,
         subclasses=["Аберрантное чародейство", "Драконье чародейство",
                     "Заводное чародейство", "Чародейство дикой магии"],
         resources=["Очки чародейства", "Метамагия", "использования Врождённого чародейства"],
         features=[
             fact("sotvorenie_zaklinanii", (164, "163", "Сотворение заклинаний (Чародей)"),
                  consts=dict(cantrips_start=4, cantrip_levels=[4, 10], prepared_level_1=2,
                              spellcasting_ability="Харизма",
                              replace_prepared=1),
                  deps=["phb2024.phb01.spell_slots"]),
             fact("vrozhdennoe_charodeistvo", (165, "164", "Врождённое чародейство"),
                  consts=dict(bonus_action=True, duration_minutes=1, spell_save_dc_bonus=1,
                              spell_attack_advantage=True, uses=2,
                              recharge="Долгий отдых"),
                  resource="Врождённое чародейство", resource_id="innate_sorcery_uses"),
             fact("istochnik_magii", (165, "164", "Источник магии"),
                  consts=dict(points_start=2,
                              points_by_level={"1": 0, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
                                               "7": 7, "8": 8, "9": 9, "10": 10, "11": 11, "12": 12,
                                               "13": 13, "14": 14, "15": 15, "16": 16, "17": 17,
                                               "18": 18, "19": 19, "20": 20},
                              slot_to_points="равно уровню ячейки",
                              recharge="Долгий отдых",
                              slot_creation_cost={"1": 2, "2": 3, "3": 5, "4": 6, "5": 7},
                              slot_creation_min_level={"1": 2, "2": 3, "3": 5, "4": 7, "5": 9},
                              max_created_slot_level=5),
                  resource="Очки чародейства", resource_id="sorcery_points"),
             fact("metamagiya", (166, "165", "Метамагия"),
                  consts=dict(options_start=2, extra_options={"10": 2, "17": 2},
                              one_option_per_spell=True, replace_on="получение уровня Чародея"),
                  resource="Очки чародейства", resource_id="sorcery_points"),
             fact("podklass_charodeya", (166, "165", "Подкласс Чародея"),
                  consts=dict(subclass_level=3,
                              subclasses=["Аберрантное чародейство", "Драконье чародейство",
                                          "Заводное чародейство", "Чародейство дикой магии"])),
             fact("uvelichenie_harakteristik", (166, "165", "Увеличение характеристик"),
                  consts=dict(levels=[4, 8, 12, 16]),
                  deps=["phb2024.phb05.ability_score_improvement"]),
             fact("charodeiskoe_vosstanovlenie", (166, "165", "Чародейское восстановление"),
                  consts=dict(recharge="Короткий отдых",
                              restore="половина уровня Чародея, округляя в меньшую сторону",
                              once_per_long_rest=True),
                  deps=["phb2024.phb01.rounding"]),
             fact("voploshenie_charodeistva", (166, "165", "Воплощение чародейства"),
                  consts=dict(point_cost=2, metamagic_max_per_spell=2)),
             fact("epicheskii_dar", (166, "165", "Эпический дар"),
                  deps=["phb2024.phb05.epic_boon"]),
             fact("misticheskii_apofeoz", (166, "165", "Мистический апофеоз"),
                  consts=dict(one_free_metamagic_per_turn=True)),
             fact("akkuratnoe_zaklinanie", (166, "165", "Аккуратное заклинание"),
                  consts=dict(cost=1, targets_max="модификатор Харизмы, минимум 1",
                              auto_success=True, no_damage=True)),
         ]),
]


def page_index() -> dict:
    """Карта: текст evidence -> PDF-страница и печатный номер страницы."""
    text = EVIDENCE.read_text(encoding="utf-8")
    pages = {}
    printed = {}
    for block in re.split(r"^=== PDF PAGE (\d+) ===$", text, flags=re.M):
        pass
    matches = list(re.finditer(r"^=== PDF PAGE (\d+) ===$", text, flags=re.M))
    for i, m in enumerate(matches):
        page = int(m.group(1))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        pages[page] = body
        head = [line.strip() for line in body.splitlines() if line.strip()][:2]
        printed[page] = head[0] if head and head[0].isdigit() else None
    return {"text": text, "pages": pages, "printed": printed}


def resolve_page(entry, index) -> dict:
    """Реальная PDF-страница умения и печатный номер со страницы."""
    if entry["deploys"]:
        pattern = r"^\s*" + re.escape(entry["source"]) + r"\s*$"
        hits = [page for page, body in index["pages"].items()
                if re.search(pattern, body, flags=re.M)]
        if len(hits) != 1:
            raise SystemExit(f"заголовок {entry['source']!r}: найдено {hits}, ожидалась одна страница")
        pdf_page = hits[0]
        printed = index["printed"].get(pdf_page)
        section = entry["source"]
    else:
        pdf_page, printed, section = entry["source"]
    if not FIRST_PAGE <= pdf_page <= LAST_PAGE:
        raise SystemExit(f"{entry['key']}: страница {pdf_page} вне {FIRST_PAGE}-{LAST_PAGE}")
    if printed is None:
        first = [l for l in index["pages"][pdf_page].splitlines() if l.strip()][:1]
        printed = None
        for line in first:
            if line.strip().isdigit():
                printed = line.strip()
    if printed is None:
        raise SystemExit(f"{entry['key']}: не найден печатный номер на странице {pdf_page}")
    body = index["pages"][pdf_page]
    # Проверяем, что на странице есть заголовок умения: полный текст до двоеточия
    # (для подклассов — часть после двоеточия).
    title = section.split(":")[-1].strip() if not entry["deploys"] else section
    title = re.sub(r"\s*\((?:Бард|Варвар|Воин|Волшебник|Друид|Жрец|Колдун|Монах|Паладин|"
                   r"Плут|Следопыт|Чародей|\d+-й уровень)\)$", "", title).strip()
    if title and title not in body:
        raise SystemExit(f"{entry['key']}: на стр. {pdf_page} нет заголовка {title!r}")
    return dict(pdf_page=pdf_page, printed_page=str(printed), section=section)


def build_rule(slug: str, entry: dict, cls: dict, index: dict) -> dict:
    src = resolve_page(entry, index)
    prefix = "phb2024.phb03.%s." % slug
    formal = []
    for op in entry["ops"]:
        # Схема v2 задаёт константы операции плоскими значениями, поэтому в
        # константы идут только скаляры; таблицы по уровням остаются в
        # rule_parameters правила.
        constants = {key: value for key, value in entry["consts"].items()
                     if not isinstance(value, (dict, list))}
        constants["procedure_id"] = "%s%s" % (slug, "_" + entry["key"])
        formal.append({"op": op, "inputs": [], "constants": constants})
    semantic = entry["questions"]
    blockers = [
        "%s: нет хендлера и процедуры для %s — ни одно состояние персонажа "
        "(уровень класса, ресурсы, слоты) движок Nat20 не ведёт" % (ENGINE, entry["source"][2] if not entry["deploys"] else entry["source"]),
        "процедура %s%s зарегистрирована только в этой карте: в vendor/nat20 её нет, "
        "подключение требует расширения ядра и нового digest" % (slug, "_" + entry["key"]),
        "сверка с SRD_CC_v5.2.1.pdf ограничена названием умения "
        "(evidence/phb-2024-ru/phb03-srd-crosscheck.json); RU-формулировка не сверена пословно",
    ]
    if entry.get("note"):
        blockers.insert(0, entry["note"])
    facts, parameters = split_facts(entry["consts"])
    return {
        "id": prefix + entry["key"],
        "rules_family": "dnd2024",
        "name": "%s: %s" % (cls["name"], src["section"]),
        "kind": entry["kind"],
        "classification": {
            "mode": entry["mode"],
            "semantic_required": entry["mode"] in ("hybrid", "adjudicated"),
            "formal_required": entry["mode"] in ("deterministic", "hybrid"),
            "reason": ("Числа берутся из текста PHB 2024 (см. facts); применение к существу "
                       "и выбранные варианты требуют решения Мастера") if entry["mode"] == "hybrid"
                      else "Числа и порядок шагов заданы текстом PHB 2024 (см. facts)",
        },
        "conditions": [],
        "triggers": ["%s.%s" % (slug, entry["key"])],
        "formal_operations": formal,
        "semantic_questions": semantic,
        "facts": facts,
        "rule_parameters": parameters,
        "constraints": [],
        "exceptions": [],
        "duration": entry["duration"],
        "resources": ([{"id": entry["resource_id"], "handling": "dependency_not_extracted"}]
                      if entry.get("resource_id") else []),
        "source": {
            "document_id": "phb_2024_ru",
            "kind": "phb",
            "sha256": SHA256,
            "publication_year": 2024,
            "pdf_page": src["pdf_page"],
            "printed_page": src["printed_page"],
            "section": "%s, глава 3 «Классы персонажей»: %s" % (cls["name"], src["section"]),
        },
        "verification": "source_checked_human_pending",
        "implementation": "not_implemented",
        "auto_activate": False,
        "dependencies": entry["deps"],
        "behavior_tests": ["phb03.%s.%s" % (slug, entry["key"])],
        "readiness": {
            "status": "blocked",
            "handler": None,
            "scope": "%s: %s" % (cls["name"], src["section"]),
            "blockers": blockers,
        },
        "revision": 1,
    }


def class_attributes_rule(slug: str, cls: dict, index: dict) -> list:
    first = cls["start"]
    printed = index["printed"].get(first)
    if printed is None:
        raise SystemExit(f"{slug}: нет печатного номера на стр. {first}")
    attrs = cls["attributes"]
    facts = dict(attrs)
    facts["subclass_level"] = cls["subclass_level"]
    facts["subclasses"] = cls["subclasses"]
    facts["pdf_span"] = list(cls["pages"])
    return [
        {
            "id": "phb2024.phb03.%s.class_attributes" % slug,
            "rules_family": "dnd2024",
            "name": "%s: Ключевые атрибуты" % cls["name"],
            "kind": "procedure",
            "classification": {
                "mode": "deterministic",
                "semantic_required": False,
                "formal_required": True,
                "reason": "Таблица «Ключевые атрибуты» задаёт точные значения "
                          "(Кость хитов, спасброски, владения) без выбора Мастера",
            },
            "conditions": [],
            "triggers": ["%s.class_attributes" % slug],
            "formal_operations": [{
                "op": "registered_procedure",
                "inputs": [],
                "constants": {"procedure_id": "%s_class_attributes" % slug},
            }],
            "semantic_questions": [],
            "facts": facts,
            "rule_parameters": {"class_resources": {
                str(i): name for i, name in enumerate(cls["resources"], 1)}},
            "constraints": [],
            "exceptions": [],
            "duration": "not_applicable",
            "resources": [],
            "source": {
                "document_id": "phb_2024_ru",
                "kind": "phb",
                "sha256": SHA256,
                "publication_year": 2024,
                "pdf_page": first,
                "printed_page": str(printed),
                "section": "%s, глава 3 «Классы персонажей»: Выбор %s..." % (cls["name"], cls["genitive"]),
            },
            "verification": "source_checked_human_pending",
            "implementation": "not_implemented",
            "auto_activate": False,
            "dependencies": [],
            "behavior_tests": ["phb03.%s.class_attributes" % slug],
            "readiness": {
                "status": "blocked",
                "handler": None,
                "scope": "Проецирование классовых атрибутов на лист персонажа",
                "blockers": [
                    "%s: движок Nat20 не хранит класс, уровень и владения персонажа — "
                    "Кость хитов и спасброски класса не из чего взять" % ENGINE,
                    "таблица мультиклассирования главы 2 (PDF 34-49) в карту не входит и "
                    "здесь не исполняется",
                ],
            },
            "revision": 1,
        }
    ]


NESTED_KEYS = ("die_by_level", "uses_by_level", "damage_bonus_by_level", "points_by_level",
               "types_by_level", "bonus_by_level", "martial_arts_die_by_level", "slot_table",
               "dice_by_level", "prepared_by_level", "free_casts_by_level", "slot_creation_cost",
               "slot_creation_min_level", "effects", "shapes_by_level", "extra_options")


def split_facts(consts: dict) -> dict:
    """Факты для карты правил. Схема v2 пускает в :facts только скаляры и
    плоские массивы скаляров, поэтому таблицы по уровням уходят в rule_parameters как
    «уровень=значение» — без потери чисел и без изменения схемы."""
    facts, tracker = {}, {}
    for key, value in consts.items():
        if key == "procedure_id":
            continue
        if isinstance(value, dict):
            tracker[key] = {str(level): ("|".join(map(str, item)) if isinstance(item, list)
                                         else "|".join(f"{k}={v}" for k, v in item.items())
                                         if isinstance(item, dict) else str(item))
                            for level, item in value.items()}
        elif isinstance(value, list):
            facts[key] = [("|".join(f"{k}={v}" for k, v in item.items())
                           if isinstance(item, dict) else str(item)) for item in value]
        else:
            facts[key] = value
    return facts, tracker


def build(force: bool) -> int:
    index = page_index()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    total = 0
    for cls in CLASSES:
        rules = class_attributes_rule(cls["slug"], cls, index) + [
            build_rule(cls["slug"], entry, cls, index) for entry in cls["features"]]
        ids = [row["id"] for row in rules]
        if len(ids) != len(set(ids)):
            raise SystemExit(f"{cls['slug']}: дубли id")
        payload = {"schema_version": "2.1.0", "rules": rules}
        path = OUT_DIR / cls["file"]
        if path.exists() and not force:
            raise SystemExit(f"{path} уже есть; запускать с --force")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        written += 1
        total += len(rules)
        print(f"{cls['file']}: {len(rules)} правил")
    print(f"итого: {written} файлов, {total} правил")
    return total


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    build(args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
