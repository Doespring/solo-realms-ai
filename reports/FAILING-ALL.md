# Полный прогон движка Nat20 — все падения с именами

Дата: 29.10.2026. Ветка `work/nat20-engine`. Окружение `.venv` этой папки,
`dnd5e_engine`/`dnd5e_srd_data` избираются из `vendor/` (md5 `build_spec.py`
совпал с site-packages до прогона и после каждой правки прогнан `sync_engine.sh`).

## Метод

Полный прогон одним процессом умирает по памяти (3.9 ГБ VPS, свободно ~150 МБ).
Поэтому — пакетами по 3 файла, `--tb=line`, лог каждого пакета и JSONL-запись
результата (`~/.hermes/cache/scratch/nat20run/`). Тест-сьют прогонялся в
несколько заходов; падения в разных пакетах перепроверены поштучно.

## Итог

| | |
|---|---|
| файлов в прогоне | **295** (все `tests/test_*.py`) |
| пакетов | 99 |
| passed | **1993** |
| failed | **120** |
| errors | **10** |
| skipped | **34** |

`failed`/`errors` — счётчики pytest по пакетам; строк `FAILED`/`ERROR` в логах
**130** (одна строка = один тест). После починки квот (см. ниже) два из них
стали зелёными.

## Одна причина на 90 падений: на этом сервере нет папки `rules_data`

`rules_data/` — данные книг (50 JSON, ~1.2 МБ), в git её нет ни в одном коммите
(`git ls-tree HEAD` папку не знает). Копия есть только в соседнем потоке
`/home/hermes/projects/SoloRealmsAI-app/rules_data`. Перенос не делался:
запрещено трогать соседнюю папку иначе как на чтение, а 1.2 МБ копировать в
чужой поток без разрешения нельзя.

Две формы отказа:

1. **ImportError при сборе** (`test_hybrid_rules_integration.py`,
   `test_hybrid_rules_runtime.py`, `test_phb_chapter_one.py`,
   `test_schema_validator.py`) —
   `from rules_data.runtime... import ...` → `ModuleNotFoundError: No module
   named 'rules_data'`. Модуль не импортируется, до кода тестов дело не доходит.
2. **ValueError → HTTP 409** (`test_hybrid_rules_production.py` — 48 падений,
   `test_phb_chapter_one_production.py` — 41, `test_pdf_world_http.py` — 1).
   `solo/hybrid_rules.py:verified_pack()` читает `rules_data/v1/...` и
   `rules_data/v2/...`; файла нет → `ValueError("Engine dependency is
   unavailable")` → глобальный обработчик в `solo/app.py:117` превращает
   `ValueError` в **409 Conflict**. Поэтому падает первый же запрос в
   `authorize()` (`GET /api/hybrid-rules/verified`, строка 65 теста), и вся
   остальная цепочка (ACL, replay, откаты, strict-типы) валится следом —
   это не 48 разных багов, а один корневой отказ.

**Это не «само пройдёт»**: без папки эти тесты не позеленеют. Нужен либо
перенос `rules_data/` в этот поток, либо решение, что эти тесты здесь не
прогоняются.

## Что сломала правка квот (починено, `d2c0f1b`)

| тест | было | причина | вердикт |
|---|---|---|---|
| `test_nat20_metamagic.py::test_a_sorcerer_ten_cannot_take_a_fifth_metamagic` | DID NOT RAISE | `_check_choice_quotas` обрезал `taken` до квоты **до** проверки переполнения — 5-й метамагик молча проглатывался | сломано моей правкой квот → **починено** |
| `test_nat20_metamagic.py::test_an_eleventh_invocation_is_refused` | DID NOT RAISE | то же: 11-е владение исчезало вместо отказа | сломано моей правкой квот → **починено** |
| `test_nat20_species_choice.py::test_goliath_cannot_take_every_ancestry_benefit` | DID NOT RAISE | то же, 6 преимуществ предка вместо отказа | сломано моей правкой квот → **починено** |
| `test_nat20_level_twenty.py::test_every_class_reaches_level_twenty_with_valid_choices[fighter]` | дефект 54 вернулся | квота суммировалась по **всем** пулам источника, и Fighter забирал оба общих стиля | сломано моей правкой квот → **починено** |

Доказательство регрессии (проверено, не на глаз): на `build_spec.py` из
`f5c569e^` те же файлы дают `1 failed, 29 passed` — падает только
`level_twenty[fighter]`, а metamagic и goliath проходят. На `f5c569e` —
наоборот. После правки: `1 failed, 85 passed` в восьми связанных файлах,
единственный оставшийся — `test_multiclass_counts_every_class` (см. ниже).

**Причина, одним абзацем.** В `_check_choice_quotas` (r113a) пики считались
так: `taken = [...][:quota]` — срез **до** проверки `len(taken) < ...`, из-за
чего переполнение становилось невозможным по построению, и проверка
`at most N` не могла сработать никогда. Плюс `quota` считалась как сумма по
всем `reached`-пулам источника сразу, поэтому Fighter и Champion (оба
называют одни и те же четыре стиля) делили один счётчик. Починено двумя
правками: (1) квота считается **по одному пулу**, переполнение проверяется по
каждому пику до любого среза; (2) «общий» пик определяется по множеству
**различных источников**, а не по числу пулов, и переполнение судится только
по эксклюзивным пикам этого источника — общий пик достаётся тому пулу,
который его забрал (это и есть смысл дефекта 54).

## Остальные падения — поимённо

Все перечисленные ниже **не связаны с правкой квот**: они падают одинаково
на `f5c569e^` и на текущем коде (проверено там, где это было неочевидно).

### Требуют отсутствующую папку `rules_data` (90 тестов)

1. `ERROR tests/test_hybrid_rules_integration.py` — `ModuleNotFoundError: rules_data`.
2. `ERROR tests/test_hybrid_rules_runtime.py` — то же.
3. `ERROR tests/test_phb_chapter_one.py` — то же.
4. `ERROR tests/test_schema_validator.py` — то же.
5. `FAILED tests/test_hybrid_rules_production.py` — 48 тестов
   (`test_routes_are_registered_but_inactive_by_default`,
   `test_real_http_roll_event_save_and_reopen_without_private_data`,
   `test_session_and_campaign_acl_is_enforced_on_reads_and_replays`,
   `test_revoked_access_never_returns_an_old_event[grant|session|expiry]`,
   `test_exact_replay_and_changed_command_replay`,
   `test_causes_are_bound_to_existing_engine[6 параметров]`,
   `test_internal_condition_stays_in_engine`,
   `test_versions_and_fingerprints_rejected_before_rng[3]`,
   `test_edition_is_checked_in_data_and_campaign`,
   `test_changed_or_missing_dependencies_fail_closed[3]`,
   `test_unregistered_operations_are_rejected_by_http_contract[4]`,
   `test_raw_ai_mechanics_and_definition_uploads_are_not_accepted[4]`,
   `test_proposal_never_commits_or_executes_text`,
   `test_explicit_confirmation_and_strict_types[4]`,
   `test_origin_policy_applies_to_new_routes`,
   `test_rollback_and_retry_use_existing_transaction`,
   `test_replay_after_undo_is_rejected`, `test_grants_do_not_survive_restart`,
   `test_binding_has_exactly_the_checked_dependencies`,
   `test_invalid_activation_never_creates_a_grant[3]`,
   `test_missing_definition_fails_closed`,
   `test_ruleset_version_change_invalidates_existing_grant`,
   `test_acl_rechecked_inside_commit_boundary[2]`,
   `test_concurrent_commands_cannot_both_commit_the_same_state_version`,
   `test_native_command_copy_cannot_bypass_operation_validation`)
   — **причина одна**: `409` на `GET /api/hybrid-rules/verified` (строка 65).
6. `FAILED tests/test_phb_chapter_one_production.py` — 41 тест (все имена в
   логе `b080.txt`) — та же одна причина `409`.
7. `FAILED tests/test_pdf_world_http.py::test_confirmed_2014_world_http_starts_explicit_2024_hero_without_battle_and_replays`
   — `409` от того же `verified_pack()`.

Вердикт по всем 90: **тест требует отсутствующую папку `rules_data`**.

### Сломано раньше (кодом, не мной; не чинил)

| тест | причина | вердикт |
|---|---|---|
| `test_nat20_item_rest_recovery.py::test_multiclass_counts_every_class` | тест от 23.10 (`e1d0897`) строит Warlock/Paladin при `charisma=12`; коммит `aea7d5a` от 28.10 ввёл правило «13 в основных характеристиках» и паладин законно требует cha 13 → `ValueError: multiclass into 'paladin' needs 13 ...; short on charisma`. Тест не обновили. | сломано раньше |
| `test_nat20_release_manifest.py::test_a_changed_engine_file_breaks_the_release` | тест правит файл в `vendor/`, а `solo.nat20_content.release()` проверяет **установленное** дерево `site-packages` (`installed = Path(dnd5e_engine.__file__)` с `9723a04`). Правка `vendor/` без `sync_engine.sh` невидима → порча проходит молча. Проверка `verify_tree(vendor/...)` вручную **ловит** порчу (`Content differs from pinned SHA: activities/dice.py`), так что сканер исправен — дефект в тесте/контракте синка, а не в движке. | сломано раньше (дефект теста) |
| `test_desktop.py::test_legacy_import_coherent_databases_profiles_dpapi_and_no_runtime` | `RuntimeError: Windows DPAPI unavailable; no plaintext secret fallback` (`solo/cloud_settings.py:99`) — тест требует Windows-крипто, на Linux нечем. | сломано раньше (среда) |
| `test_smoke_relocation.py::test_relocated_command_cli_help_and_pointed_package_imports` | ожидает `scripts.command_smoke --help` с кодом 0 и `--output`; получает ненулевой код — пакетная перекладка не завершена. | сломано раньше |
| `test_nat20_bless.py::test_pending_release_isolated_from_preview7_and_production` | `solo/nat20_api.py:137: ValueError: Nat20 initial replay diverged` — воспроизведение старого fixture расходится с текущим движком. | сломано раньше |
| `test_nat20_dash.py::test_closed_contract_old_fixture_and_legacy_boundary` | то же: `Nat20 initial replay diverged`. | сломано раньше |
| `test_nat20_prototype.py::test_atomicity_and_old_fighter_release_survive` | то же. | сломано раньше |
| `test_preview9_clarification.py::test_pending_context_survives_real_process_and_retries` | то же. | сломано раньше |
| `test_nat20_bless.py::test_http_cast_slot_memory_acl_new_process_replay_undo_and_atomicity` | `subprocess.TimeoutExpired` — дочерний процесс `restart-turn` не уложился в 60 с. Причина вероятная: хост перегружен (в момент прогона свободно ~150 МБ) + старт воркера 1.5–3.5 с на процесс. **Не доказано, что это не флак** — нужен одиночный перегон на свободной машине. | не подтверждено, требует перепроверки |
| `test_nat20_prototype.py::test_diverse_native_build_encounter_action_restart[wizard]` | `cast_failed ... magic-missile ... reason: target_invalid` — волшебник не находит цель для снаряда. | сломано раньше |
| `test_prototype_integration.py::test_wizard_ui_api_magic_save_new_process` | тот же `target_invalid`. | сломано раньше |
| `test_prototype_memory.py::test_real_experimental_wizard_multiple_native_targets_restart_continue` | тот же `target_invalid`. | сломано раньше |
| `test_nat20_api.py::test_native_goblin_hit_uses_projected_bonus_and_damage` | `('hero' == 'hero' and 6 == 4)`: урон гоблина 6 вместо ожидаемых 4 (d6=2 + DEX 2). | сломано раньше |
| `test_creation_options_batch.py::test_exact_prechange_options_and_one_release_one_read_per_selected_file` | `default_build` даёт дополнительные ключи сверх разрешённых `{background_slug, ability_score_method}` — форма ответа разошлась с baseline. | сломано раньше |
| `test_desktop_pdf_smoke.py::test_registered_pdf_hook_and_recreated_app_continue_without_repreparing` | `assert` в `solo/desktop_observations.py:116` — повторное приложение переподготавливает PDF. | сломано раньше |
| `test_first_playable.py::test_exact_duplicate_and_explicit_npc_binding_are_repaired_without_guessing` | `assert False` (строка 52) — дедупликация локации/NPC не сработала. | сломано раньше |
| `test_frozen_pdf_smoke_fixture.py::test_bundled_pdf_transport_through_registered_api_and_continue` | `503` от `/command-turn` — замороженная фикстура PDF. | сломано раньше |
| `test_playable_ai_readiness.py::test_native_wizard_packets_include_bounded_capabilities_and_engine_outcomes` | `scripts/playable_ai_readiness.py:183: AssertionError: rejected` — пакет волшебника отвергнут. | сломано раньше |
| `test_player_visible_text.py::test_gm_and_error_paths_store_filtered_text` | ожидает вызов `edit_narrative(...)` в исходнике обработчика — его там больше нет. | сломано раньше |
| `test_preview8_release.py::test_real_preview7_fighter_restores_and_continues_without_stand` | `Nat20 initial replay diverged`. | сломано раньше |
| `test_preview9_clarification.py::test_native_reproducer_then_known_action`, `::test_previous_unknown_question_does_not_swallow_new_action`, `::test_true_clarification_uses_joined_answer_not_standalone` | `examples/lantern-post/scene.json` отсутствует (папка есть только в `SoloRealmsAI-app`). | нет папки `examples/lantern-post` |
| `test_preview7_acceptance.py::test_lantern_launch_dialogue_check_reward_restore` | то же — `FileNotFoundError: examples/lantern-post/scene.json`. | нет папки `examples/lantern-post` |
| `ERROR test_playable_scene_memory.py` — 6 тестов (`test_scene_formats_...`, `test_npc_knowledge_...`, `test_command_and_reward_retries_...`, `test_narration_never_awards_...`, `test_undo_fork_checkpoint_...`, `test_reward_requires_native_victory_...`) | сборка модуля читает `examples/lantern-post/scene.json`. | нет папки `examples/lantern-post` |
| `test_prototype_gm.py::test_prepared_native_world_lookup_mechanics_and_restart` | `assert 'rejected' == 'resolved'` — подготовленная сцена не резолвится. | сломано раньше |
| `test_sse_503_ui.py::test_unavailable_model_during_narration_answers_200_with_error_frame` | кадр ошибки приходит с текстом «Механика сохранена…», тест ждёт порядок `['token','error']` — стрим отдал не то. | сломано раньше |
| `test_pdf_nat20_start.py::test_incomplete_real_source_cannot_be_promoted[missing-...]` | `assert (not True)` — неполный источник всё-таки повысился. | сломано раньше |

## Гипотеза о «полосе ~30 подряд идущих F» на ~26%

**Подтверждена, и объяснение простое.** Это `tests/test_hybrid_rules_production.py`
(пакет 19, 48 падений из 51 теста) — прогресс-бар `...FFFF...F(×48)`.
Весь класс падает целиком по **одной** причине: fixture `live` создаёт приложение,
и **первый же** HTTP-запрос после `POST /api/advanced/session` —
`GET /api/hybrid-rules/verified` → `verified_pack()` → `ValueError` (нет
`rules_data/`) → обработчик `solo/app.py:117` → **409**. А `authorize()` —
общий помощник, через который идут почти все тесты файла, поэтому 409
воспроизводится в каждом, и падения идут подряд, а не вразброс.

Полоса на «~26%» — это позиция пакета 19 в прогоне: 19-й из 99 пакетов
(≈19%), а если считать по тестам — 48 падений из ~130 всех строк `FAILED`
(≈37%); в любом проценте от 19 до 37 эта строка прячется, но файл один.

## Про «само пройдёт»

Ничего в списке не помечено как «само пройдёт». Два пункта требуют
перепроверки одиночным запуском на свободной памяти (timeout в
`test_http_cast_slot_memory_acl_...`), остальные — детерминированные отказы
с названной причиной.

## Что сделано по итогам

- `vendor/nat20/.../build_spec.py` — квота считается по пулу, переполнение
  проверяется до среза, общий пик принадлежит забравшему пулу, «общий»
  определяется по множеству различных источников.
- `vendor/nat20/PATCHES.json` — перепинен (`build_spec.py`, sha256).
- Проверено: `test_nat20_metamagic.py`, `test_nat20_species_choice.py`,
  `test_nat20_level_twenty.py`, `test_nat20_item_rest_recovery.py`,
  `test_nat20_class_spells.py`, `test_nat20_expertise.py`,
  `test_nat20_multiclass.py`, `test_nat20_background_languages_tools.py`
  → `1 failed, 85 passed`; единственный падающий —
  `test_multiclass_counts_every_class` (сломан раньше правилом 13).
