# Working prototype integration — 2026-10-18

## Rules — factual Python seam (prototype-1)

Implemented signatures on `solo.nat20_api.Nat20GameEngine`:

```
catalog(category=None, query='', limit=50, offset=0) -> {release,total,entries:[...]}
catalog_detail(identity: str) -> catalogue entry plus data: typed canonical JSON
build_schema() -> actual native CharacterBuildSpec JSON Schema
preview_build(build: dict, prepared_spells=()) -> {release,build,sheet,hero,warnings}
api.create_experimental_campaign(name, *, build, encounters, checks=None,
    prepared_spells=(), seed=None, title='Experimental campaign',
    description='Experimental Nat20 campaign') -> existing Store campaign dict
capabilities(current: Store campaign dict) -> compact build/sheet/refs/actions/targets/issues
api.execute(cid, GameCommand(request_id,expected_version,action)) -> unchanged result
api.restore(cid) -> {runtime,snapshot,version}
```

Catalog category: `classes,species,subclasses,backgrounds,feats,features,items,
spells,monsters,conditions,traits,actions`. Stable ID `category:slug`; each entry
has `id,slug,name,category,rules_version,source,execution_status,known_issue,parameters`.
`catalog_detail('classes:wizard')` exposes native advancement/options for builder
choices. Search is `catalog(category=...,query=...,offset=...,limit=...)`.
Canonical item discriminator is `data.item_kind == 'weapon'` (not `data.type`).
Do not send full catalogue each GM turn. Native `CharacterBuildSpec` choices are
passed unchanged, not a new UI-owned schema. Classes/species come from catalogue.

`encounters` is a dict by stable ID (e.g. `forest`), each value:
`{grid:<native GridScene>,hero_position:{x,y,z:0},actors:[{id,monster:<slug>,
position:{x,y,z:0},xp_value:0}],reward?:<existing Reward>}`. Up to 20 monsters;
HP/AC/actions derive from pinned content. XP is explicit author input (default 0).
Important native gap: monster attacks require precomputed numeric to-hit in
`EncounterMemberSpec`; canonical omits it and its default is zero. Optional
actor fields `attack_bonus:int` and `attack_bonus_source:<CampaignSource>` accept
explicit trusted author inputs together. Missing input causes a concrete atomic
refusal when that monster actually attempts an attack, not fabricated +0 combat.
This is reported in monster catalogue entries and is never a GM action field.
Instantiation, movement and targeting still work. Local Foundry derivation of
the missing input is not ported in this package.
`checks` maps ID to `{kind:'skill'|'ability'|'saving_throw',skill:string|null,
ability:'str'|'dex'|'con'|'int'|'wis'|'cha'|null,dc:int|null,source:
{kind:'authored_scenario',id,version,note}}`. Empty checks generates ordinary
skill, ability and `save-str` etc. checks without invented DC; source is author.
Optional check flags `advantage`/`disadvantage` are author data; Native applies
them, including cancellation. They are not extra parameters of an AI action.
Build/encounters/check DC/rewards are creation input, never arguments of AI tools.

Common commands are unchanged. Broad public native operations use
`{kind:'native_action',operation:<native intent_type>,weapon_id?,spell_id?,
target_id?,target_ids?,slot_level?,item_id?,feature_id?,activity_id?,position?,
direction?,excluded_target_ids?,charges_to_spend?,pool_points?,...}`.
Exact DTO is `solo.game_commands.NativeAction`; import it / `tool_schemas()`.
Operation is the pinned PlayerIntent Literal, not free text. Reference IDs must
be in build equipment / derived features / selected prepared spells. No damage,
DC, stat overrides, expressions, code, or arbitrary state patch. Pending legacy
CastSpell/Bless DTO is not the experimental magic path. Known broken `hide`
and `bless` refuse individually. `pass` is upstream intent; `end_turn` additionally
advances native NPC policy until hero turn. Skills/saves use `check` + registered ID.

Snapshots keep memory-v1 and field ACL. Projection uses native initiative/HP,
movement/action counters, spell slots, concentration and active effects; use
existing `/commands` and `/command-turn`. Generic server button candidates may
use these imported DTOs, `preview(current,command)` or worker availability; do
not implement legality on client. Capabilities exposes only authorized hero
build and references; NPC private counters are excluded by `visible(snapshot)`.
This package does not touch UI/GM/memory/packaging-owned files. New experimental
campaigns select `prototype-srd-2024-v1`; old fighter fingerprints never migrate.
Implementation smoke/status: `PROTOTYPE_ENGINE.md` (updated at stage completion).

## UI / screen adapter (prototype-4)

- Delivered entry: `solo.prototype_ui.register_prototype_ui_api(app, store, game)`.
  UI owns that separate display/routing adapter, `playable_ui.py`, `play_screen.py` and `web/*`;
  app.py registration stays with the integrator. No alternative engine command schema.
- Existing `screen-v1` (`snapshot.hero`, resources, combat/actors, events, `commands[]`) remains
  the visual contract. Buttons submit the offered `parameters` and `expected_version` to
  `/campaigns/{id}/commands`; text uses existing `/command-turn`. Save/restore are services.
- Bound to the actual Rules seam above: `/prototype/options`, `/prototype/catalog/{category}`,
  `/prototype/build/preview`, `/prototype/campaigns`, `/campaigns/{id}/prototype`. Builder input
  embeds imported `CharacterBuildSpec`; actions remain imported `NativeAction` via `/commands`.
- Ordinary new game exposes class/species/subclass/background/equipment/build choices and
  server preview, followed by authored/sandbox or confirmed prepared-world selection. Prepared
  starts call existing `prototype_prepared.launch_prepared`, including revision/confirmation.
  Existing importer and old Solo builder remain secondary paths. No cloud calls in the smoke.
- **NPC continuation input delivered:** `NewGameInput.encounter_attack_bonuses` maps monster
  slug to an explicit author integer, `encounter_attack_sources` maps slug to source text.
  Ordinary UI has a “Параметры встречи из вашего источника” disclosure. These optional values
  are forwarded unchanged as native `PrototypeActor.attack_bonus/attack_bonus_source`.
  No default +0 or calculation. Missing input keeps the concrete native limitation visible.
  Generic `/commands` label now supports `native_action`, fixing the pre-engine KeyError.
- Real browser smoke has created dwarf Rogue and Wizard, used a prepared NPC world, observed,
  checked, talked, opened sheet/inventory, saved/reloaded, rejected Hide and cast Magic Missile
  through selected native references/targets. Own test: `tests/test_prototype_ui.py`.
- Tests/evidence use a unique `prototype-4` stand, `SOLO_TEST=1` before app imports. Until the
  Rules seam exists, any response fixtures are labelled as such; final check must use real Nat20.

## GM / AI (prototype-2)

- Existing entry remains `await Game.play_command(campaign, request_id, text, expected_version)`.
  Engine commands import `solo.game_commands`, backend selection imports `solo.command_backend`.
- `SceneCompiler.command_context` will supply scoped memory/current snapshot, public current
  scene, current hero sheet and a bounded command/capability summary. No full corpus per turn.
- Narrative intents (`conversation`, `observe`) are routing DTOs in `model_protocol`, not
  engine commands. Mechanical actions remain the Rules-owned `Action`/`GameCommand`.
- Public registration to be delivered: `solo.game.register_prototype_gm_api(app, store, game,
  *, preparation=None)`. Integrator registers it; AI does not edit `app.py`.
- Need from Rules: published capability/search/detail interface and new-campaign signature
  accepting an explicit validated build + world/encounter data. Until published, use existing
  command schemas and imported backend; do not modify Rules DTOs/runtime or invent handlers.
- Need from world/memory: public scene + NPC-scoped context through `EngineMemoryAdapter`;
  conversation records remain statements (not mechanical facts), existing Memory API only.
- All test profiles temporary `prototype-2`; `SOLO_TEST=1` before app imports. No user data,
  key reads, paid calls, or parallel full regression. Report `docs/PROTOTYPE_GM.md`.


## Host / integrator (prototype-5)

- Delivered: `create_app(db, llm=None, *, local_port=8000, experimental=False, offline_mock=False)`.
  Experimental factory registers the published UI and GM functions above as they become
  importable; `preparation` is the existing app.state.preparation. `/api/prototype/status`
  distinguishes pending vs registered. Module import without SOLO_DATA_DIR opens no DB.
- `Desktop.cmd` / `Start.cmd`: one native host/backend/frontend, explicit new prototype
  profile. `Prototype-Offline.cmd`: same source host with mock. `desktop_main.py
  --experimental --offline-mock --data-dir <new-empty-path>` is the isolated smoke entry.
- 3 narrow host tests PASS plus actual loopback HTTP startup/static/settings PASS.
  No engine/Game/memory/web ownership changes. Full suite and EXE not run yet.
- Default cloud settings remain editable/disabled until user configuration; no key copying
  and no paid warmup. Existing ROOT/data is rejected by the app factory.
- Remaining integration depends on actual Rules catalog/build/capability services and
  `register_prototype_ui_api` / `register_prototype_gm_api` implementations. Reuse one
  pinned runtime; package the new explicit canonical release alongside old minimal data.


### Integrator live seam finding — UI owner (prototype-5)

Actual registered API creates wizard + two rats, observation/check/start work.
`POST /api/campaigns/{id}/commands` with
`{kind:native_action,operation:use_item,item_id:potion-of-healing,target_id:hero}`
returns HTTP404 `{detail:"'native_action'"}` **before engine execution**.
Exact cause: `solo/playable_ui.py:78` indexes `labels[body.action.kind]`, while
its local label map has no `native_action`. This is presentation/routing ownership
(UI), not a missing Nat20 item handler. Integrator did not edit that file.
Please support a generic label (or .get fallback) while retaining typed DTO,
backend selection, expected_version and existing command validation.
Reproducer/evidence: tests/test_prototype_integration.py and
reports/prototype-5/integration.xml/log. No full regression/build launched.


### Integrator stage 2 finding — Rules/UI (prototype-5)

UI label fix is present and works. The same unchanged positive API path now passes
potion native_action, checkpoint and a new-process exact Nat20 replay. It then fails
`end_turn` HTTP409: `Native monster weapon attack bonus is unbound in canonical data;
supply trusted encounter attack_bonus + attack_bonus_source. No state committed.`
Evidence: reports/prototype-5/integration-fixed.xml/log; same test retained positive.
This is a **continuation blocker for normally created catalog encounters**, not just
an optional item's failure: NewGameInput/encounter_monsters currently cannot supply
those declared actor fields, so ordinary UI has no way to resolve the missing input.
Please bind a verified supported encounter source or explicit trusted authored
attack_bonus/source input through ordinary creation UI. No made-up +0/default and no
skipping the NPC attack or altering the pinned runtime. It is fine to limit supported
NPC attacks honestly; at least one ordinary diverse-class encounter must continue.
Integrator will rerun the same ordinary API path once this seam is provided.


### Integrator resolved route + input binding; next GM seam

Both prior UI findings now resolved by UI owner. `integration-bound.xml`: 1 actual
wizard/two-rat API/Nat20 path PASS (22.77s): observation, save-dex, native healing item,
checkpoint, new OS process equal replay, end_turn, Magic Missile, combat end and rest.
Explicit author fixture bonuses/sources were supplied through the new ordinary input,
not invented SRD defaults. Earlier RED protocols remain historical and are not summed.

Next confirmed RED `social-topic.xml`: marked source→prepared Rogue/halfling→question
“Спрашиваю Дорна про мост” returns technical `goals` npc_state rather than available
source npc_knowledge about the bridge. State/ACL survive; presentation packet/fallback
finding sent to AI owner for correction. Positive assertion remains, not weakened.
Common full regression/build is not started until this seam and owner delivery.

### GM social-topic fix — prototype-2, 18 October 2026

Delivered fix for the unchanged positive test
`tests/test_prototype_integration.py::test_prepared_source_rogue_social_memory_through_registered_api`.
Prepared Rogue/halfling source world now answers the bridge question from the
NPC's authored knowledge instead of quoting `npc_state goals=[]`.

Changed: `solo/prototype_gm.py`, `tests/test_prototype_gm.py`,
`docs/PROTOTYPE_GM.md`, and this appended integration note.

- Existing NPC-scoped Memory Context Builder remains the only retrieval source.
  Presentation accepts factual `npc_knowledge`, `knowledge`, `fact` records for
  NPC dialogue; state-transition audit rows, player statements and technical
  mechanics are not quotable knowledge. No Memory/store/schema/ACL changes.
- Explicit authoring labels `Описание:` / `Знание:` are removed in the outgoing
  presentation copy. Literal source sentences are ordered by lexical overlap
  with the complete player utterance (including persisted clarification).
  IDs/provenance/source_ref remain attached; source records are not rewritten.
- Same packet and existing `verify`/fallback serve both CloudLLM and PreviewMock.
  Technical state quotation still fails canon verification; absent eligible
  knowledge produces `Не знаю.`. Dialogue remains a statement, never world truth.
- Targeted run: unchanged prepared-source API test + own GM tests: **6 passed**,
  existing Starlette/lark deprecations only. Includes two-topic source selection,
  state-audit exclusion, no-knowledge fallback, rogue/wizard cloud mock/native
  actions and restart/replay. No full gate, live/paid request, EXE build or server.

No Memory change is required for this reproduced seam. The contract used is
`engine_context(... npc_id=..., query=utterance).memories` with factual, typed
knowledge and intact provenance. This adapter does not bypass retrieval limits,
read private world fields directly or merge another NPC's audience.
Own temporary stand: `%TEMP%/solo-prototype-2-tests`, tests finished; retained
for coordinated cleanup. Foreign `tmp-social-topic` and incident/user data untouched.

### GM regression contract — clarification and social version

The former lantern-only dialogue shortcut returned prose without a journal event.
General prototype conversation now records an actual social journal event through
existing Store.record_scenario, so campaign version increases by one. This is an
intentional contract: statement memory has a real event/branch position, and replay
survives restart without attaching new speech to an unrelated mechanical event.
Hero, Nat20 runtime, inventory and world facts remain byte-for-byte equivalent.
The API returns the new state_version; clients must use that version for the next
command. Retrying the same request returns its original result without another
version increment or statement. No engine arithmetic or restore behavior changed.
The old lantern version assertion is being updated with these stronger invariants;
its existing combat/reward/save/restore checks remain intact.

### GM regression freeze — 18 October 2026

**FROZEN / targeted green.** No further source edits or checks running in this chat.
Production change in this follow-up: `solo/preview_mock.py` only. It recognizes
Russian tracks/attention wording only when registered `perception` is available,
keeps unknown phrases as clarification, rejects explicit undeclared capabilities
honestly, and prevents an old check question from intercepting a new independent
Dash/action. General scenes and social-topic packet filtering remain in place.

Tests changed here: semantic context assertions in `tests/test_command_cycle.py`
(public scene/sheet/declared commands, SECRET_CANARY exclusion unchanged), and
`tests/test_preview7_acceptance.py` social version contract plus unchanged
Hero/Nat20/economy/facts and idempotent retry. No changes here to root-owned exact
tool-list assertions in preview8/preview9/dash mapping tests.

Local targeted results, **33 passed** total:

- preview9 clarification + preview dash mapping + command cycle: **28 passed**, 24.16s;
- unchanged prepared-source social API + two topic/canon tests + legacy lantern
  conversation/combat/reward/save/restore + **real old Dash browser test**:
  **5 passed**, 55.63s. `tests/test_dash_ui.py` ran and passed (not skipped).

All subsequent lantern command versions come from the existing helper's current
campaign version, so no further fixed-version assertions were shifted. The social
step alone adds exactly one version; its replay adds none. Existing restore checks
pass. Source Dash browser replay/stale behavior passed end-to-end.
No full suite, EXE/build, provider keys, paid/live AI or foreign tmp-social-topic.
Browser test's own server shut down in its finally block. Temporary own stand
`%TEMP%/solo-prototype-2-tests` retained for integrator-coordinated cleanup.

### Integrator — final portable acceptance, 18 October 2026

One 2.2.0-prototype.1 EXE/ZIP produced; final results and limitations are in reports/prototype-5/RELEASE.md. Full once1140/15; targeted corrections7/GM33/final6/package1 separately, not summed. Frozen worker5 and actual native builder/NPC/check/multi-actor combat/checkpoint/new-process continuation passed. Spell success included detected manual input, observed and DB-confirmed, not an unattended GUI claim. Cloud setup screen is accessible, unconfigured, zero calls. All own windows closed. Original48states unchanged and preservation incident remains open. Coordinated new prototype TEMP removal31 directories rejected blocked by policy; no retry/workaround. Full evidence/ZIP and native profile with manual input retained.


### PDF → confirmed world → Nat20 contract (GM, 18 October 2026)

Implementation scope: `prototype_prepared.py`, Preparation/backend adapters and own
smokes. No app.py/web/engine DTO/runtime/memory/source_extraction edits.

- Reuse CreationService → PreparationPipeline → PreparedWorldValidator output;
  expose `prepared_start_readiness(preparation, draft_id)` (local only, safe counts,
  run/revision, source edition, concrete blockers and blocked operations).
- Existing `launch_prepared(... confirm, expected_revision, build, encounters,
  checks=None, prepared_spells=(), seed=None)` gains optional
  `mechanics_ruleset=None`, `rules_confirmed=False`.
  Existing confirmed dnd2024 drafts retain compatibility. For source/recorded
  dnd2014, require explicit mechanics_ruleset='dnd2024', rules_confirmed=True.
  Do NOT rewrite draft.ruleset or source AdventureDefinition.ruleset. The new
  campaign runs dnd2024 and stores both editions + provenance in preparation.
- Add `PreparationPipeline.confirm_world(draft_id, *, expected_revision, run_id,
  confirm)` to promote only the existing fully source-validated world after an
  explicit local confirmation. No inference or new extraction pipeline. Partial
  runs, unknown essential dependencies, stale selection and names-only worlds
  remain blocked with concrete reasons; no ready=True bypass.
- Never invoke the document-pinned first_playable observation entry as the common
  Nat20 start. Keep its legacy guards intact. Shared start needs a grounded public
  opening, grounded locations/references and any mandatory start dependencies.
- Source statblocks/tables/effects are NOT automatically transferred. Unsupported
  mechanical profiles block their specific operation; public world/NPC knowledge
  remains usable if start does not require the blocked operation. Mechanical
  encounters supplied for Nat20 are explicit independently confirmed 2024 input.
- Preserve scoped memory/provenance, existing launch recovery/idempotence and old
  campaign behavior. app/API wiring is integrator-owned; UI is interface-owned.

**Rules seam:** current PrototypeDefinition.encounters has min_length=1. To start a
confirmed world without inventing a battle, Rules must allow an empty authored
encounter map and continue rejecting start_combat with an unregistered ID. GM
will not modify that DTO/runtime or insert a dummy enemy. This does not disable
mechanics: registered checks/rests/items and explicit supported encounters remain.

Contract refinement after inspecting real source wire: compact SourceExtraction
contains only name/location/start/span IDs, so it CANNOT supply NPC knowledge or
public descriptions. Add PrepareRequest.world_structure=false (backward compatible).
Explicit true reuses the SAME PreparationPipeline block/resume machinery and the
existing PreparationBatch/source_input/decode + PreparedWorldValidator contract;
wire fingerprint is source-world-v1, so compact cached names never masquerade as
full structure. Consent/budget still required; no automatic live second pass.
`preparation.preparation_pipeline` references the existing instance for adapters.
The compact source_extraction parser/repair and first_playable guards are unchanged.
`confirm_world` is local; returns existing Preparation draft/revision. For simple
creation launches the confirmation token is mandatory, source body.ruleset stays
2014, and cross-edition launch requires explicit mechanics_ruleset/rules_confirmed.
API wiring for prepare flag + local confirm/readiness is integrator-owned.

### PDF/Nat20 backend handoff — GM

Delivered common backend contract and report `docs/PDF_NAT20_START.md`.
Changed only own preparation/backend adapters: preparation.py (confirmation field),
preparation_contracts.py (world_structure flag), preparation_pipeline.py (same
validated full-contract mode + local confirm/readiness), prototype_prepared.py,
and own tests/test_pdf_nat20_start.py. No app.py/web/engine DTO/runtime/memory or
source_extraction hotfix edits; concurrent dirty files/ZIP preserved.

Final targeted command (SOLO_TEST=1 before app imports):
`pytest -q -p no:cacheprovider tests/test_pdf_nat20_start.py tests/test_pdf_preparation_p0.py::test_failed_middle_block_does_not_discard_or_rebill_good_blocks_on_resume tests/test_pdf_preparation_p0.py::test_truncated_block_splits_without_losing_text_and_survives_restart tests/test_preparation_pipeline.py::test_consent_edition_and_cancel_do_not_mutate_campaign_or_sources tests/test_preparation_pipeline.py::test_source_text_and_player_request_are_both_sent_quotes_checked_and_unsupported_quarantined tests/test_prototype_integration.py::test_prepared_source_rogue_social_memory_through_registered_api`
Result: **11 passed**, 24.39s; existing Starlette/lark deprecations only. Real
synthetic text PDF/import, MockTransport, source-2014 + explicitly confirmed
mechanics-2024 Rogue, private NPC dialogue, native check, save/new-process native
continuation, draft linkage recovery and different-build refusal passed.
Negative names-only/missing NPC location/essential unknown mechanic/mandatory
legacy startup procedure and optional-mechanic non-global blocking passed.

**Remaining owned elsewhere:** app/API/web connection to confirm_world/readiness;
Rules min_length=0 for encounter map (currently min_length=1). The GM adapter
already accepts no encounters; no dummy battle inserted. Until Rules updates that
DTO, zero-encounter launch is a documented blocker, not claimed playable. The
positive smoke uses an explicit independently authored optional native profile.

Cleanup: no own active tasks/servers. Verified exact own temp path, no reparse
point, zero matching Python processes. One PowerShell deletion request rejected
blocked by policy; no retry/workaround. Retained own stand:
C:\Users\iwa41\AppData\Local\Temp\solo-pdf-nat20-start-tests.
No full/EXE/live/paid, original profiles/data/WAL and foreign stands untouched.

### PDF world HTTP bridge — active integrator contract

- GET `/api/creation/drafts/{id}/world-readiness`: existing safe local
  prepared_start_readiness projection. Always scope/world_ready/ready/revision/reasons/
  blocked_operations; run_id/source_ruleset/mechanics_ruleset exist when world is available.
- POST `/api/creation/drafts/{id}/confirm-world`:
  `{expected_revision,run_id,confirm:true}`. Returns existing public CreationService.get
  metadata `{id,revision,updated,name,description,campaign,status,files}` plus
  `world_readiness`. Never exports private Draft.body, source quotes or GM result.
- Existing `/prepare` accepts explicit `world_structure:true` and prior consent;
  separate fingerprint, no automatic prepare/retry/full-book request from local endpoints.
- `/prototype/options` includes confirmed simple worlds even before hero creation:
  row id=`prepared:<draft_id>`, revision, scope=`world`, source_ruleset, mechanics_ruleset,
  world_ready/ready; no forced hero_name. Names-only / unconfirmed worlds stay excluded.
- `/prototype/campaigns` keeps existing native build request and adds, for simple world,
  `mechanics_ruleset:'dnd2024',rules_confirmed:true,hero_confirmed:true,
  background_confirmed:true,hero_background:string`. UI must explicitly collect both
  rule and hero/background confirmation. Native preview validates sheet first; existing
  Draft.save stores player identity/background, preserving source ruleset, followed by
  existing launch_prepared. No user-supplied mechanical Hero overrides or legacy launch.
- `encounter_monsters:[]` means `encounters:{}` with no dummy mandatory battle. Rules DTO
  min_length=0 delivery is frozen and the positive zero-encounter HTTP case passed.
- Original /readiness, legacy preparation and /character/confirm remain separate.
  The latter launches legacy, so is not used for this Nat20 creation path.

No release/build is authorized at this stage; tests use isolated synthetic profiles only.

### PDF world HTTP bridge — source/API ready

Implementation above is now active in creation.py/prototype_ui.py. No web/GM/DTO
edits by integrator. Public confirmation response is metadata + world_readiness only.
Explicit identity/background binding preserves world_confirmation/run/hash and the
source2014 edition. Early worker refusal rolls back only the staged identity; published
world linkage uses existing recovery. Blank name and unknown Native build reject early.

API evidence: [HTTP report](../reports/pdf-world-http/REPORT.md), http.xml (2 PASS,
1 test-only assertion typo), fix.xml (2 PASS: corrected rollback and legacy compatibility),
validation.xml (1 PASS: additional invalid inputs). Do not sum these overlapping runs.
Positive actual HTTP path: confirmed source2014 → explicitly accepted Native2024 Rogue,
encounters={} → check/replay → factory restart/repeated start, one campaign. No additional
mock inference in readiness/confirm/native commands; no actual providers or user data.

Rules is frozen: [zero-encounter delivery](PDF_NAT20_RULES.md), 3 targeted PASS; new
HTTP zero-encounter case passes. GM backend: [PDF/world contract](PDF_NAT20_START.md),
11 targeted PASS reused without rerunning. UI/browser delivery passed; see the final handoff below.
Names-only cached blocks do not satisfy world confirmation; a new structural request
needs explicit consent and distinct fingerprint. No new EXE or archive at this gate.


### PDF world — functional UI handoff ready

[UI delivery](PDF_NAT20_UI.md) is frozen: 4 targeted pytest checks passed. Explicit
fixtures remain labelled; the real loopback API/pinned Nat20 browser case confirms
world → explicit confirmations → Rogue → observe/NPC/check → checkpoint → new Python
process → exact state and continuation. No user book/provider or native EXE claim.
Processes are stopped; prior policy-blocked cleanup is not retried/bypassed.

HTTP: [integration report](../reports/pdf-world-http/REPORT.md).
Backend: [PDF/world validation](PDF_NAT20_START.md), 11 targeted PASS reused.
Engine: [zero encounter delivery](PDF_NAT20_RULES.md), 3 targeted PASS reused.
UI: [real API trace](../reports/pdf-nat20-ui/real-results.json),
[fixture trace](../reports/pdf-nat20-ui/fixture-results.json),
[restarted scene](../reports/pdf-nat20-ui/real-world-rogue-restart.png).
Test runs remain separate; there is no repeated full regression or release claim.
The common Windows delivery was authorized and built once as 2.2.0-prototype.3.

### PDF world — Windows preview handoff

See [release report](../reports/pdf-world-release/RELEASE.md) and
[status](../reports/pdf-world-release/STATUS.json). One selected regression: 60 PASS,
not the full suite. The actual frozen EXE passed the narrow new-API/resource/consent
and zero-encounter check on its own mock profile; its window and backend are closed.
The full positive synthetic PDF journey remains source/browser evidence, not a native
real-book or live-AI claim. ZIP CRC and 2187 file hashes passed; .1/.2 are preserved.
Production is frozen for coordinator handoff; no source archive or upload was made.

### Next playable session — server preparation resume contract

The coordinator authorized work in the original source tree after preserving the
.3 source snapshot; the .3 bundle and Drive snapshot remain immutable. This section
describes subsequent source work, not the contents of the released .3 EXE.

Accepted backend handoff: [PDF preparation resume](PDF_PREPARATION_RESUME.md).
`pipeline.status/latest` expose `preparation_mode` (`world_structure`,
`source_extract`, or `unknown`) and saved `source_ruleset` (`dnd2014`, `dnd2024`,
or null), including queued/partial/restart. Creation GET already projects latest
under `preparation`; no additional production projection is required at this gate.
These fields do not imply world readiness or reveal private source/result bodies.

Resume must explicitly send the selected boolean `world_structure`, saved source
edition, consent, and request ID. Unknown mode requires a player's explicit choice;
it must not silently become compact extraction. Missing mode after a world/unknown
run is refused before provider HTTP. Local readiness is free; repeated request ID
is idempotent, and the existing fingerprint mechanism controls reuse of good blocks.
No storage migration or fingerprint reset was introduced.

Reused backend evidence: final targeted run **10 PASS**, 21.90 s, including synthetic
partial/restart/resume, public creation projection, privacy, idempotence and the
previous PDF/Nat20 continuation case. The earlier overlapping 11-PASS run is separate;
neither is a full suite. No tests were rerun by integration and no live API/build was
performed. UI cache-loss/resume and native NPC action handoffs remain pending; there
is no new combined acceptance or release claim yet.

### Next playable session — common gate planned

Rules handoff [Native playable next](NAT20_PLAYABLE_NEXT.md) is engine-frozen.
Seven unique latest cases passed across separate owner runs; historical XML files
also contain earlier failures and are not called a clean suite. The final
`test_nat20_playable_next.py` will be included in the single common selected gate,
alongside PDF HTTP, command-cycle, host and packaging checks, as authorized.
[UI PDF handoff](PDF_PLAY_TODAY_UI.md) has two passing owner tests; its final
automatic NPC binding UI is awaiting freeze. Previous .3 60-PASS acceptance remains
a separate baseline and is not added to these counts.

The missing UI bridge is a narrow public GET
`/api/prototype/catalog/{category}/{slug}` delegating to the existing
`Nat20GameEngine.catalog_detail` in a worker thread. Monster detail includes the
existing `attack_profile` and `known_issue`; list/options do not compute every NPC.
No new rule arithmetic or error-handler override is introduced. The common HTTP
test covers selected Wolf and rejected unknown slug/category with no campaign mutation.

After UI/Rules freeze and a green selected gate, one .4 preview is authorized in
`dist/playable-next` and `reports/playable-next-release`. .1/.2/.3 and the frozen
Drive source snapshot must remain unchanged. Native acceptance uses a new explicit
offline profile; full source/browser PDF evidence and frozen host evidence remain
separate. No full-suite run or live/paid AI request is authorized for this gate.

### Next playable session — .4 accepted within stated scope

UI freeze and final selected-profile handoff: [Native monster UI](NAT20_MONSTER_UI.md),
one real browser/API smoke PASS (24.09 s), no endpoint/game-response fixtures.
Wolf gets Native +4/provenance without override; Knight shows its unbound reason.

One common selected regression completed: **24 PASS**, 112.51 s, three existing
Starlette/lark warnings. It includes final Rules module6 + old fighter1, PDF HTTP4,
command-cycle7, packaging2 and host4. No full suite or repeated owner smoke series.
Backend10, PDF UI2, Native UI1 and .3 baseline60 remain separate evidence, not a sum.

One .4 Windows build: [release report](../reports/playable-next-release/RELEASE.md),
[status](../reports/playable-next-release/STATUS.json),
[verification](../reports/playable-next-release/verification.json).
The actual EXE passed the existing explicit offline QA hook: local UI/resources,
HTTP consent refusal, zero-encounter Native campaign and a selected Wolf with
Native-derived +4/canonical provenance. Window/backend stopped; ready12.0706 s;
own ledger0 provider calls. Full positive PDF preparation in the frozen EXE is
unverified because bundled PreviewMock lacks that preparation transport. Its guard
was preserved; positive synthetic PDF/restart/resume/save evidence remains source/browser.

ZIP CRC and2190 file hashes passed. Frozen .1/.2/.3 ZIP/EXE/manifests, pinned runtime
and production snapshot are unchanged; no user SQLite/AppData connection or live/paid AI.
Production is frozen again for handoff. No upload/source archive was made here.
Cleanup of this task's verified build/tmp-regression/native-profile was rejected
once as blocked by policy (67,804,688 bytes); no retry/workaround. Previous blocked
stands and the frozen Drive snapshot were not touched; the data incident remains OPEN.

### .5 matched-source gate — explicit synthetic PDF fixture

Authorized next scope: [Native action controls](NATIVE_ACTION_PARAMETERS_UI.md),
owner source freeze with1 PASS/50.90 s; real charges/direction/pool actions and
lost-response retry are distinguished from its explicit serialization/409 fixtures.
Previous .4 24-PASS gate and earlier owner backend/UI/Rules checks are reused.

[PDF fixture contract](PDF_FROZEN_SMOKE_FIXTURE.md) adds a closed MockTransport in
`smoke_fixture.py`, not a prepared-world injection. Desktop wiring requires the
explicit combination `--smoke-test`, `--smoke-scope pdf-world`, `--offline-mock`,
`--data-dir`, and `--smoke-pdf-phase prepare|continue`. Prepare refuses normal/default
or nonempty profiles; continue requires the successful first-phase marker. The
ordinary player/cloud/OfflineMock paths remain unchanged.

The new hook uses upload/import/preparation/confirmation/Native/command/save APIs.
The planned second EXE process compares the saved native hash, canonical screen and
replayed runtime before a further check, with no new preparation. Source API tests
use a labelled fake renderer and recreate the app; actual frozen/new-process proof
must be recorded separately. Source-only PASS does not authorize that native claim.

One narrow matched gate will run the new hook/guard tests plus existing host/package
checks after owner AI/UI freeze, followed by one isolated .5 build in
`dist/pdf-playable` / `reports/pdf-playable-release`. .1–.4 and Drive .3 remain
immutable. Synthetic mock requests/cost entries must be distinguished from real
provider calls and payment; user books/live AI are outside this acceptance.

### .5 accepted — frozen synthetic PDF and new-process continuation

Final matched-source gate: **11 PASS**, 22.51 s, three existing warnings; not a
full suite. Owner controls1/factory1 and baseline .4 24 remain separate evidence.
One .5 build, two actual frozen EXE processes (PID8220/24988): upload/import the
synthetic PDF through normal preparation/consent APIs, confirm source2014 world,
explicit Native2024 Rogue, observation/NPC/check/save version4; next OS process
matched native hash/canonical screen/Native replay and continued to version5.
New process preparation count0; both windows/backends stopped. Native renderer is
real, but this automated path used embedded HTTP, not a new full button-flow UI pass.

Accepted mock packets8 versus synthetic ledger entries9: one pdf-look narrator
HTTP400 is an explicit fixture coverage gap (observation place/atmosphere packet
does not match its narrow narrator predicate). Existing source-grounded
engine_fallback returned game API200/status observation. This is not error-free AI;
no external fallback, real provider/payment0. See synthetic-ledger-summary.json.

[Release](../reports/pdf-playable-release/RELEASE.md),
[status](../reports/pdf-playable-release/STATUS.json),
[local verification](../reports/pdf-playable-release/verification.json),
[independent root verification](../reports/pdf-playable-release/root-independent-verification.json).
ZIP CRC/2193 hashes, pin, source snapshot and old .1–.4 artifacts passed; Drive .3
was not touched. Production frozen for handoff. User data incident remains OPEN.
One own cleanup attempt was policy-blocked:265 files/60,738,588 bytes retained in
this task's build/tmp-regression/native-profile; no workaround or old-stand retry.

### Stage3 routing source freeze — 18 October 2026

Current master directive supersedes historical incident/release workflow notes above:
the old incident was archived by its owner; no new EXE/ZIP is authorized.
[Routing delivery](NAT20_STAGE3_ROUTING.md) passed one targeted run: **9 PASS**,
104.02 s, three existing warnings. Engine14 from Stage2 is reused, not rerun.

Common execute delegates before the legacy transaction to the existing Native
single atomic journal/event/snapshot path. Common resolve preserves a list result
and is a pure disposable-worker probe for Native; availability writes no journal,
memory, resources or persistent/global RNG. Preview and actual execute were compared
for roll/rest/battle/feature/spell; duplicate/stale/rollback/undo/fork/new-process
replay and minimum legacy compatibility passed. Corrupt Native presence fails closed.
Game/creation already route explicit Native campaigns; incompatible legacy Hero
saves/creation DTOs are not silently migrated. No rule arithmetic/runtime fork.

Six changed source/test hashes match the tested snapshot. See
`reports/stage3-routing/freeze.json` for the exact handoff. Cleanup of only this
task's tmp-targeted was rejected once:22 files/6,197,248 bytes retained, no workaround.
No full suite/build/live AI. Overall Stage3 waits for other owners; root owns the
Solarimus AI copy and final stage acceptance.

### Stage3 options performance — bounded source freeze

[Options delivery](NAT20_STAGE3_OPTIONS.md): one verified batch for seven categories
replaces11 repeated paginated projections. Exact pre-change DTO180070 bytes/SHA
0c0d3e93c1ec8b55d3ebaf8af6a37e6390dc61e3c8db19c4bc4585f25fdfec35 is preserved.
Every options call still verifies the release; static schema data are detached.
No new HTTP API/pagination or all-monster attack computation. Memory approved the
shared entry-projection helper ownership; pin/cache verification semantics unchanged.

Unprofiled matched options: fresh1325.697→697.672 ms (1731 mandatory SHA), persisted
new-process974.459→431.657 ms (0 SHA), repeats≈161 ms. Imports≈1 s are separate;
Windows file cache was not flushed. Persisted500-ms target passes; fresh/full cold
screen500-ms target is not achieved. Further micro-optimization stops here.

Three unique contract cases pass by latest result: initial2PASS/1test-expectation
FAIL, then only that corrected case1PASS. Tamper/re-pin invalidation and full DTO
equality passed. No whole gate/build/live API. Exact current/dependency hashes:
`reports/stage3-options/freeze.json`; its nat20_prototype SHA supersedes the prior
content-cache freeze for that file only. Own cleanup was blocked once:7files/
4,477,017bytes retained, no workaround. Root owns final acceptance/Solarimus AI copy.
