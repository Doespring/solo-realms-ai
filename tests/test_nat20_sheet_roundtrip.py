"""Требование 5 и 9: лист переживает НОВЫЙ процесс и читает старые сохранения.

Проверка идёт двумя шагами в РАЗНЫХ процессах: первый пишет лист в файл,
второй (`subprocess`) читает его заново. Тот же процесс этого не доказывает —
кэши классов и загрузчик переживают любой внутренний круговорот.

Также зафиксирована совместимость: запись, сделанная ДО появления полей
`cantrips`, `prepared_spells`, `tool_proficiencies`, `starting_equipment`,
`wealth_gp`, читается с умолчаниями, а не отвергается.
"""
import json
import subprocess
import sys
from pathlib import Path

from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, DerivedSheet, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader

ROOT = Path(__file__).resolve().parents[1]

SCORES = AbilityScores(
    strength=16, dexterity=12, constitution=14,
    intelligence=10, wisdom=10, charisma=8,
)

NEW_FIELDS = (
    "cantrips", "prepared_spells", "tool_proficiencies",
    "starting_equipment", "wealth_gp",
)


def _sheet(level=1):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="soldier",
        level=level, ability_scores=SCORES, selected_choices=("archery", "skilled"),
    )
    return derive_sheet(spec, loader=BundledAssetLoader())


def test_the_sheet_round_trips_losslessly():
    sheet = _sheet()
    assert DerivedSheet.model_validate_json(sheet.model_dump_json()) == sheet


def test_a_save_from_before_the_new_fields_still_reads():
    payload = json.loads(_sheet().model_dump_json())
    for field in NEW_FIELDS:
        payload.pop(field, None)
    restored = DerivedSheet.model_validate(payload)
    assert restored.cantrips == ()
    assert restored.prepared_spells == ()
    assert restored.tool_proficiencies == ()
    assert restored.starting_equipment == ()
    assert restored.wealth_gp == 0


def test_a_new_process_reads_the_saved_sheet(tmp_path):
    """Лист, записанный ОДНИМ процессом, читается ДРУГИМ."""
    target = tmp_path / "sheet.json"
    original = _sheet(5)
    target.write_text(original.model_dump_json(), encoding="utf-8")

    reader = tmp_path / "read.py"
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from dnd5e_engine.build_spec import DerivedSheet\n"
        "p = Path(sys.argv[1])\n"
        "print(DerivedSheet.model_validate_json(p.read_text(encoding=\"utf-8\")).model_dump_json())\n"
    )
    reader.write_text(code, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(reader), str(target)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr[-500:]
    again = DerivedSheet.model_validate_json(done.stdout.strip())
    assert again == original, "новый процесс прочитал лист иначе"
    assert again.hp_max == original.hp_max
    assert again.feats == original.feats

