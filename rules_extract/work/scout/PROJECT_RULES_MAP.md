# SCOUT — карта правил и механики Solo Realms AI

Агент: **scout**. Дата: 2026-10-23. Область: только чтение; в проекте ничего не изменялось.
Артефакты — только в rules_extract/work/scout/ (helper-скрипты _probe*.py, inventory.json).

> AGENTS.md: разработка остановлена. Документ описывает ФАКТИЧЕСКОЕ состояние кода, а не план работ.

---

## 0. TL;DR

1. Единственный «настоящий» калькулятор D&D 2024 — закреплённый **Nat20 0.7.0** (vendor/nat20,
   commit e187c813bb4e43c4e4e62942ecd83831f1cdbcaa), вызываемый через solo/nat20_api.py.
   Его корпус (dnd5e_srd_data/canonical) содержит 12 классов / 14 видов / 12 подклассов / 4 предыстории /
   17 черт / 261 фичу / 546 предметов / 339 заклинаний / 341 монстра / 15 состояний / 102 traits.
   **Но в игру подхвачена малая часть** (см. §1.2).
2. Исполняемый играбельный набор узок: воин-человек 1 уровня; longsword + chain-mail + shield;
   один фича Second Wind; заклинание Bless заблокировано; один монстр goblin-warrior (только ветка scimitar).
3. Самописные solo/combat.py (4 оружия) и solo/rulesets.py — **легаси** для «обычных» кампаний.
   AGENTS.md: старый hybrid/самописные правила не развивать.
4. Данные правил существуют в **трёх несовместимых форматах** (rules_data/v1, rules_data/v2, Nat20 canonical);
   единого нет. Полного текста в репозитории нет — только SQLite-FTS и внешние txt.

---

## 1. Что реализовано

### 1.1 Реестр возможностей — solo/rule_registry.py (38 строк)
    ROWS = (id, category, status, handler, scope, limitations)
    checks.ability        implemented  RulesetEngine.check
    combat.weapon_attack  implemented  CombatEngine.attack
    combat.turn           partial
    magic.casting         unsupported  handler=None ('Нет универсального исполнителя заклинаний')
    features.execution    partial
    conditions.execution  partial
    rest.recovery         partial
    advancement.level_up  partial      nat20_levelup.level_up (только dnd2024)
    character.level_one   partial      character_creation.evaluate

coverage() добавляет source_kind='existing_srd_engine_subset', pdf_import_can_activate=False.

### 1.2 Nat20 (единственный 2024-калькулятор)
- Вход: solo/nat20_api.py:Nat20GameEngine + solo/nat20_worker.py (JSON через subprocess).
- Playable-команды (integration/nat20/playable/capabilities.json): check, start_combat, move, dash,
  attack, use_feature:second-wind, end_turn, end_combat, short_rest, long_rest.
  Экспериментально (nat20-experimental-v1): level_up, native_action/use_feature.
- solo/game_commands.py:NativeAction.operation объявляет attack, cast_spell, use_item, move, dash, dodge,
  disengage, hide, help, ready, reaction, move_mark, use_feature, pass, drop_concentration, grapple,
  shove, stand_up, escape_grapple — контракт ШИРЕ приёмки.
- solo/nat20_content.py:CHECKS = 18 навыков + 6 характеристик + 6 save-<ability>, все DC 15 (perception 12) —
  DC зашиты, не из таблиц.
- weapons() отдаёт все оружия корпуса (86) с полями name, category, damage[].(dice,damage_type), mastery, weight.
- known_issues (nat20_api.capabilities): hide не тратит Action; bless — неверный бонус спасброска/
  концентрация/рекаст; rage — активация даёт disabled effect; spell_components — V/S/M и свободная рука не проверяются.
- Не реализовано (docs/PLAYABLE_RULES_SCOPE.md): Action Surge, Weapon Mastery, Fighting Style/feats,
  заклинания, полный конструктор, новые классы/уровни, >1 врага, смерть/спасение при 0 HP, бегство/сдача,
  экипировка через inventory, world-события, ветки монстров (Shortbow, Nimble Escape, условное преимущество).

### 1.3 Самописные модули (только для «обычных» Solo-кампаний)
| Модуль | Что делает | Границы |
|---|---|---|
| solo/rules.py | modifier=(score-10)//2, proficiency=2+(level-1)//4, check() | отказ для attack/use/other |
| solo/rulesets.py | Ruleset 2014/2024, SKILLS 18 + SKILL_NAMES ru, exhaustion 2024, d20/check/skill/initiative/saving_throw/rest | sheet().unsupported: automatic_class_progression, multiclass, feats_effects, spell_catalog, automatic_origins, equipment_effects, full_srd_combat_actions |
| solo/combat.py | бой по сетке: 4 оружия (unarmed, club, longsword, shortbow), 13 типов урона, укрытие, дальность, провок. атаки, death save, падение | нет заклинаний, фич, статблоков, длительностей состояний |
| solo/geometry.py, maps.py | LOS/свет/двери/клетки | — |
| solo/inventory.py | Item/Economy/Merchant, CATALOG = 10 предметов (club, longsword, shortbow, arrows, leather, chain_mail, shield, healing_potion, rations, rope), вес/цена/экипировка/AC/торговля | нет магических предметов |
| solo/character_catalog.py | конструктор 1 ур.: 4 класса (fighter, rogue, wizard, cleric), виды 2024 (human, dwarf), 4 предыстории (acolyte, criminal, sage, soldier), 26 заклинаний-записей, черты со статусами implemented/partial/recorded | Level Up и магия не реализованы |
| solo/progression.py | уровень из опыта (Nat20 level_for_experience) или вехи | — |
| solo/rule_knowledge.py | текстовый поиск по книгам, 18 тем (advantage, check, stealth, surprise, …) | текст не становится механикой |

---

## 2. Карта: где лежит каждая категория правил

| Категория | Где | Формат | Ключевые поля |
|---|---|---|---|
| Полный текст книг | data/rules-knowledge.sqlite3; rules_extract/text/*.txt | SQLite FTS5 / txt | books(sha,title,path,edition,kind,license,partial,pages,readable,complete,index_version); sections(id,book,page,printed,heading,text,public,topic,lead) |
| Декларативные правила v1 | rules_data/v1/ | JSON Schema 2020-12 rule.schema.json (contract 1.0.0) | schema_version,id,revision,name,edition,category,conditions,triggers,effects,duration,resources,restrictions,sources,verification,engine |
| Маршрутизация правил v2 | rules_data/v2/**/*.rules.json | routing.schema.json 2.1.0 | см. §4.1 |
| Карта покрытия | rules_data/v2/coverage/phb-01.json | coverage.schema.json 1.0.0 | bookmarks/subsections/rules{id,file,classification,dependencies,handler,test_id,pytest_node,test_kind,status,pdf_pages} |
| Типизированный корпус 2024 | vendor/nat20/packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical/<cat>/<slug>.json | pydantic-схемы schema/*.py | см. §4.2 |
| Закреплённые копии для игры | integration/nat20/imported/canonical/… (12 док.) + manifest.json | canonical + provenance/review | sha256 в manifest |
| Определения кампаний | integration/nat20/playable/*.json + registry.json | campaign.schema.json (solo/nat20_campaign.py) | schema_version='nat20-campaign-v1', rules_version, id, build, attack_weapon, features, checks, encounters |
| Метаданные Foundry-корпуса | integration/nat20/playable/search-catalog.json (708 KB, 2460 док.), pack-inventory.json (40 packs) | JSON | executable=false, import_status='metadata_only' |
| Списки заклинаний классов | integration/nat20/playable/class-spell-lists.json (877 связей, CC-BY) | JSON | — |
| Стартовое снаряжение (проза→slug) | solo/nat20_kit.py | Python dict KITS[(kind,source)] | items[(slug,count)], picks[(group,default)], gold_gp, unresolved[] |
| Стейт/события/память | solo/storage.py (schema 9/10), solo/engine_memory_contracts.py (memory-v1), data/solo.sqlite3 | SQLite + pydantic | scope, known_by, source_ref, sequence |
| Состояние Nat20 кампании | state.nat20 в журнале | nat20-state-v1 (DurableState) | release_digest, seed, hero, sheet, hit_dice, exhaustion, active_effects, combat, definition, experience, rewards[] |
| Каталог ПК-опций 1 ур. | solo/character_catalog.py | Python-код | BASE_CLASSES, SPECIES_2024, BACKGROUNDS_2024, FEATURES, SPELLS, FIGHTING_STYLES, SUBCLASSES |
| Предметы/торговля | solo/inventory.py | pydantic + CATALOG | id,catalog,type,name,quantity,weight_centilb,value_cp,properties,condition,owner,source,parameters{} |
| Источники Мастера | solo/preparation.py, data/preparation.sqlite3 | SQLite | ревизии, хеши, GM-only FTS |
| Сюжетные модули | solo/adventure_*.py, data/adventure.sqlite3 | SQLite + pydantic | AdventureDefinition, таблицы, трекеры |
| Foundry 5.3.3 паки | integration/nat20/sources/foundry-5.3.3/dnd5e/packs/{actors24,classes24,content24,equipment24,feats24,monsterfeatures24,origins24,spells24,tables24} | LevelDB | raw _source/*.yml проекции |
| Документы по дизайну | docs/*.md (60+), ARCHITECTURE.md | Markdown | — |

### 2.1 Объёмы (проверено)
- rules_data/v2: 143 правила (procedure 115, guidance 23, spell_branch 3, monster_stats 1, monster_ability 1);
  implementation: existing_engine_subset 56, kernel_verified 9, kernel_only_unexecuted 3, not_implemented 75;
  classification: deterministic 75 / hybrid 44 / adjudicated 24. Покрытие — PHB гл.1 (131 запись, PDF 8–33),
  DMG стр. 24–25/29, goblin-warrior, 4 правила PHB (advantage + 3× Wish).
- rules_data/v1: 4 записи (advantage, disadvantage, cancellation, single-die-change), PDF 13–14.
- Nat20 canonical: классы 12, виды 14, подклассы 12, предыстории 4, черты 17, фичи 261, предметы 546,
  заклинания 339, монстры 341, состояния 15, traits 102.
- data/rules-knowledge.sqlite3: 4 книги, 7503 секции; DMG неполный (185 стр.).

---

## 3. Главные пробелы для полной базы 2024

### 3.1 Данные
- Нет реестра «класс → списки заклинаний по уровням» в Solo (только class-spell-lists.json + 26 записей SPELLS).
- Нет таблиц ячеек заклинаний в Solo (ячейки — только из Nat20 derive_sheet).
- Нет таблицы Mastery оружия (8 свойств: Cleave, Graze, Nick, Push, Sap, Slow, Topple, Vex); есть только строка mastery.
- Нет таблиц CR/XP/бюджета встреч; XP встречи — «авторский параметр» (solo-road-watch@1.0.0, 50 XP).
- Нет справочника DC кроме трёх (math_handlers.lookup_dc: easy 10 / moderate 15 / hard 20, DMG стр.24).
- Нет закрытого перечня состояний Nat20: contracts.Hero.conditions = 14, маппинг фильтром в nat20_api._hero.
- Нет русских названий для 86 оружий / 546 предметов / 339 заклинаний / 341 монстра / 236 фич;
  переводы есть для 26 заклинаний, 4 классов, 2 видов, 4 предысторий (character_catalog) и 15 монстров
  (mechanics_mapping.MONSTER_ALIASES).
- Нет названий школ магии, компонентов (V/S/M + стоимость), ритуалов.
- Нет магических предметов/rarity/attunement в inventory.py, хотя канон Nat20 их содержит.
- Бестиарий для игры = 15 ручных псевдонимов в mechanics_mapping.MONSTER_ALIASES; остальное «requires adaptation».
- Нет машиночитаемых таблиц эффектов состояний (истощение — формула в rulesets.exhaustion_effects).

### 3.2 Механика
- Заклинания: magic.casting = unsupported; CastSpell/DropConcentration скрыты из tool_schemas(),
  включаются только при SOLO_TEST=1 и SOLO_NAT20_BLESS_STAND=1 (bless_stand_enabled()).
  Блокеры (docs/NEXT_RULES_GAP_REVIEW.md): Foundry system.changes[].type='add' теряются в
  dnd5e-srd-data/tools/translators/foundry.py:_passive_effects (~стр.802, читает только changes и числовой mode);
  materials.cost Bless = 0 при требовании Holy Symbol 5+ GP.
- Компоненты/свободная рука не проверяются; ритуалы/upcast не подключены.
- Weapon Mastery: записи в rule_registry нет; в capabilities.json — imported_but_not_enabled.
- Action Surge / Fighting Style / feats — recorded; Fighting Style: Defense даёт +1 AC в inventory.recalculate_ac.
- Мультикласс unsupported; подклассы в каталоге — 4 (champion/thief/evocation/life), в каноне 12.
- 0 HP: death saves есть в combat.py и dnd5e_engine/death_saves.py, но playable-путь останавливается на 0 HP.
- Бой >1 врага: PrototypeActor до 20, проверенные определения — 1 враг.
- Hide/Stealth: hide не тратит Action (known_issue).
- Время вне боя: game_minutes есть в GameEvent, граница эффектов end_combat не доказана.
- Heroic Inspiration, encounter builder/CR, таблицы лута — только текст/metadata_only.

### 3.3 Процессные
- Нет конвертера rules_data/v2 → canonical; imported/canonical — ручной отбор 12 документов.
- rules_data/v1 не принимается /api/rulesets и не является Ruleset-моделью.
- coverage.schema.json жёстко прибит const document_id='phb_2024_ru', chapter:1, pdf_range:[8,33].

---

## 4. ТОЧНЫЙ формат для новой базы правил

### 4.1 Формат A — правило поведения (rules_data/v2/*.rules.json, routing 2.1.0)
Обязательные поля правила (rules_data/v2/routing.schema.json#/$defs/rule):

    id              ^dnd2024\.[a-z0-9_.]+$
    rules_family    const "dnd2024"
    name            1..2500
    kind            procedure | spell_branch | monster_stats | monster_ability | guidance
    classification {mode: deterministic|adjudicated|hybrid, semantic_required:bool,
                    formal_required:bool, reason:text}
                    (deterministic => false/true; adjudicated => true/false; hybrid => true/true)
    conditions      [{fact:^[a-z][a-z0-9_.]*, operator:eq|gte|lte|in, value}]
    triggers        [^[a-z][a-z0-9_.]*, >=1, unique]
    formal_operations [{op:<16 enum>, inputs:[id], constants:{}}]  (>=1 если formal_required)
    semantic_questions [text]                                      (>=1 если semantic_required)
    facts           {id: scalar|array}
    constraints     [text]
    exceptions      [{when, consequence}]
    duration        instantaneous|per_test|per_action|source_dependent|persistent_guidance|not_applicable
    resources       [{id, handling:registered_engine_only|dependency_not_extracted}]
    source          {document_id: phb_2024_ru|dmg_2024_ru_v1_1|mm_2025_ru_v1_1, sha256, publication_year,
                     pdf_page, printed_page, section}
    verification    source_checked_human_pending|text_checked_human_pending|translation_ambiguity
    implementation  existing_engine_subset|kernel_only_unexecuted|not_implemented|kernel_verified
    auto_activate   const false                     ← всегда
    dependencies    [id]                            ) только в 2.1.0
    behavior_tests  [text]                          )
    readiness {status: adapter_verified|kernel_verified|engine_subset_verified|
                       adjudication_required|blocked,
               handler: null|<29 enum, напр. CombatEngine.attack | RulesetEngine.d20 | math.sum_dice>,
               scope: text, blockers: [text]}       )
    revision        integer >= 1                    )

op enum: select_d20, ability_modifier, sum_dice, compare_threshold, group_success, bounded_value, lookup_dc,
resource_change, delegate_registered_spell, registered_bonus_action, wish_stress, scale_integer, percentile,
percentage_event, replace_draw, registered_procedure.
ВАЖНО: 104 из 143 правил используют registered_procedure (делегируют Python-обработчику
rules_data/runtime/math_handlers.py). Новое правило реально исполняется только при handler из enum, иначе not_implemented.

### 4.2 Формат B — сущность (Nat20 canonical; pydantic в dnd5e_srd_data/schema/*.py)
Общие поля: slug, name, description, provenance{source,source_url,srd_version,license_tag,ingest_version,ingest_date},
review{known_divergence,requires_user_decision}.

| Категория | Поля |
|---|---|
| classes | identifier, hit_die, primary_ability{all\|value}, saving_throws, granted_features[{level,slug,ref_type,optional}], feature_choices, subclass_identifiers, advancement[{id,level,type,title,value,configuration{items,optional,sizes,spell},hint,class_restriction}], spellcasting{ability,progression}, wealth, entry_kind |
| species | creature_type{value,subtype,custom}, size, movement{walk,fly,swim,climb,burrow,hover}, senses{blindsight,darkvision,tremorsense,truesight,passive_perception}, languages, trait_grants, granted_features, advancement, feature_choices |
| features | feature_type, foundry_id, source_slug, uses, advancement, passive_effects, activities[{id,kind,name,activation{type,value,condition,reaction_conditions,override},consumption{scaling,spell_slot,targets},range{value,units,special,override},target{affects,prompt,override,template},duration{concentration,units,value,special,override},damage{parts,critical,include_base},healing{number,denomination,bonus,custom,types,scaling},attack{type,ability,bonus,flat,critical},effects,applied_effects,uses{max,spent,recovery},visibility{identifier,level,require_attunement,require_identification,require_magic},flags,img,sort,description}] |
| items | item_kind, weapon_category, damage_parts[{dice,damage_type}], versatile_damage, mastery, properties, range, cost_gp, weight, magical, magical_bonus, rarity, requires_attunement, attunement_constraint, uses, passive_effects, activities[] |
| monsters | creature_type, creature_size, alignment, ac, hp, hp_dice, ability_scores{str..cha}, saving_throws{}, skills{}, senses{}, languages, cr, proficiency_bonus, damage_resistances/vulnerabilities/immunities, condition_immunities, movement{}, actions[{name,slug,kind,description,mechanic,activities,recharge,uses_per_day,legendary_cost}], special_abilities, lair_actions, legendary_actions, legendary_action_uses, legendary_resistance_uses, spellcasting_ability |
| spells | те же activities[] + level/school/components (materials.cost); для Bless доп. effect changes[].type='add' |
| conditions/traits/feats/backgrounds/subclasses | свои схемы schema/{condition,feat,background,class_}.py |

КРИТИЧНО: effects[].changes[] с типом 'add' теряются встроенным переводчиком; новая запись эффекта должна
нести числовой mode ИЛИ нормализатор с собственным SHA (docs/NEXT_RULES_GAP_REVIEW.md).

### 4.3 Формат C — игровое определение кампании (campaign.schema.json, nat20-campaign-v1)
    обяз.: id (<=80), title (1..120), description (1..1500), source{kind,id,version,note},
           build, attack_weapon, features[], checks{}, encounters{}
    build: species_slug*, class_slug, classes{slug:level}, subclass_slug, level 1..20,
           ability_scores{strength..charisma}*, ability_score_method: standard_array|point_buy,
           equipment[], selected_choices["skill:athletics",…], background_slug, hp_mode:"fixed",
           hp_rolls{}, ac_calc_mode, attuned_items[]
    checks[id]: {kind: skill|ability|saving_throw, skill?, ability?, dc 1..40, advantage, disadvantage}
    encounters[id]: {grid{width<=80,height<=80,cell_size_ft,blocked_cells[],wall_segments[],
                     cover_cells{},difficult_terrain_cells[],lighting{},default_lighting,
                     obscurement_cells{},sunlight}, hero_position{x,y,z},
                     actors[{id,monster,position,xp_value,attack_bonus?,attack_bonus_source?}],
                     reward{id,kind:'native_encounter_xp',source}}
    опц.: rules_version:'dnd2024', schema_version:'nat20-campaign-v1',
          spellcasting{content_release,prepared,holy_symbol,hands,environment}
Регистрация: playable/registry.json definitions[id] = {file, sha256, verification:'behaviorally_verified', evidence[]}.

### 4.4 Минимальный совместимый формат для НОВОЙ базы (предложение)
1. Справочный уровень — строки rules-knowledge.sqlite3:sections (book,page,printed,heading,text,public,topic).
   Только текст; механикой не является и не исполняется.
2. Декларативный — rules_data/v2/*.rules.json (routing 2.1.0) с обязательными source{sha256,pdf_page,printed_page},
   dependencies, readiness{status,handler,blockers}, behavior_tests, revision, auto_activate:false.
   facts — числа/строки; formal_operations — только из enum; иначе implementation=not_implemented.
3. Исполняемый — Nat20 canonical документ (provenance+review) → integration/nat20/imported/manifest.json (sha) →
   playable/capabilities.json sources[] (foundry_pack, foundry_id, license, sha256, raw_sha256) →
   определение кампании + registry.json.
4. Локализация — пара name_en + name_ru в каждом декларативном правиле; для сущностей canonical —
   отдельный файл i18n (НЕ переписывать canonical), чтобы сохранить sha/pinning.

---

## 5. Опорные пути
- Контракты команд: solo/game_commands.py, solo/nat20_contracts.py, integration/nat20/schemas/*.schema.json.
- Реестр возможностей: solo/rule_registry.py (единственный список status/handler).
- Пиннинг: vendor/nat20/UPSTREAM.json, integration/nat20/dependencies.json, integration/nat20/releases/*/manifest.json.
- Тексты: rules_extract/text/{SRD521,CONV,PHB2024ru,GM2024ru,AG-MM}.full.txt и *.pages.jsonl.
- Не путать AG-MM.full.txt (AG-MM v1.1, 386 стр.) и mm_2025_ru_v1_1 в routing.schema.json
  (sha 28e9491b39b3df24b255f62973f95e83abd0395e4ac3d2d10d7163ead47146ca, 386 стр.).

## 6. Чего нет в источнике (не выдумывать)
- data/rules-knowledge.sqlite3: DMG 2024 НЕПОЛНЫЙ — 185 стр. (в ТЗ — 342).
- rules_data/v2/evidence: MM 2025 как книги нет, только выписки goblin-warrior.
- В проекте нет машинных таблиц PHB (классов/уровней, XP) — только тексты и Nat20-код.
