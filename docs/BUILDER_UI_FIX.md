# Constructor UI fix — contract / work in progress

UI owner: `web/prototype.js`, `web/creation.js`, corresponding CSS and own tests. API owner remains `solo/prototype_ui.py` elsewhere. No backend edits here.

Observed `build_schema`: level integer 1..20; ability_scores uses local `$ref` to `$defs.AbilityScores`, whose current integer properties have no min/max. UI will resolve `$ref` and `anyOf` and apply actual server constraints; it will not invent ability bounds.

Minimal additive metadata requested from backend owner: subclass rows `class_slug` + `min_level` (canonical source, nullable if unavailable); ability bounds in existing `build_schema.$defs.AbilityScores.properties.<ability>` are preferable. If native schema cannot express author-input constraints, additive `builder_constraints.ability_scores` / `builder_constraints.level` schemas and `builder_constraints.subclasses` list are acceptable; exact fields to follow backend contract. UI needs class compatibility and level requirement, no formula or derived-sheet copy. Invalid stored values will remain visible with inline errors; only a deliberate class/level edit clears an incompatible subclass.

Current UI bug: Number('') silently writes 0; numeric inputs have no integer step/required and score bounds absent, preview is type=button and bypasses form validity, subclass list is unfiltered. Changes will use actual HTML validity plus inline errors before preview/create, without erasing name/other input.
