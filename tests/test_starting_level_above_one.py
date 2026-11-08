"""A character can start above level 1, and a caster can be built by hand."""
import sys
from pathlib import Path
import pytest
from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nat20_builds import legal  # noqa: E402

LOADER = BundledAssetLoader()
# The engine accepts the standard array only. The fighter's ability increases go
# to strength, so charisma keeps the 15 and can never hit the cap of 20.
FIGHTER = dict(strength=14, dexterity=13, constitution=12,
               intelligence=10, wisdom=8, charisma=15)
WIZARD = dict(strength=8, dexterity=13, constitution=12,
              intelligence=15, wisdom=14, charisma=10)


def sheet_for(klass, level, scores):
    spec = CharacterBuildSpec.model_validate(
        legal(class_slug=klass, level=level, species_slug='human',
              ability_scores=scores))
    return spec, derive_sheet(spec, loader=LOADER)


@pytest.mark.parametrize('level', [1, 3, 5, 10])
def test_a_legal_start_above_first_level_derives_a_complete_sheet(level):
    spec, sheet = sheet_for('fighter', level, FIGHTER)
    data = sheet if isinstance(sheet, dict) else sheet.__dict__
    assert spec.level == level
    assert data['proficiency_bonus'] == 2 + (level - 1) // 4
    assert data['ability_scores']
    assert data['features']
    if level > 1:
        assert len(data['features']) > 1, 'a higher level must bring its features'


def test_a_caster_built_by_hand_is_legal_and_carries_its_spell_feature():
    spec, sheet = sheet_for('wizard', 5, WIZARD)
    data = sheet if isinstance(sheet, dict) else sheet.__dict__
    assert data['proficiency_bonus'] == 3
    features = str(data['features'])
    assert 'spellcasting' in features
    assert 'arcane-recovery' in features, 'the fifth level brings its caster features'


def test_the_engine_refuses_a_level_that_disagrees_with_its_classes():
    from pydantic import ValidationError
    payload = dict(legal(class_slug='fighter', level=1, species_slug='human',
                         ability_scores=FIGHTER))
    payload['level'] = 5
    with pytest.raises(ValidationError):
        CharacterBuildSpec.model_validate(payload)
