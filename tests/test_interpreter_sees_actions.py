"""The interpreter must be told which actions exist.

The prompt said 'неизвестная механика — other' but never listed the checks,
weapons or encounters the snapshot offers, so the model could not name a fight
or a dagger. It also offered a made-up 'basic' encounter the engine had never
registered.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_interpreter_prompt_lists_the_action_sources():
    text = (ROOT / 'prompts' / 'qwen_interpreter_v1.txt').read_text(encoding='utf-8')
    for field in ('available_checks', 'available_weapons', 'available_encounters'):
        assert field in text, field


def test_the_snapshot_carries_encounters_so_the_model_can_name_one():
    from solo.engine_memory_contracts import GameSnapshot
    assert 'available_encounters' in GameSnapshot.model_fields


def test_no_made_up_encounter_is_offered():
    source = (ROOT / 'solo' / 'nat20_screen.py').read_text(encoding='utf-8')
    # A hard-coded {'basic': None} advertised a fight that did not exist.
    assert "{'basic':None}" not in source.replace(' ', '')
