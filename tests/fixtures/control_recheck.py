"""Recompile the saved control blocks locally: no network, no spend."""
import json, os, sys
from pathlib import Path

os.environ.setdefault('SOLO_TEST', '1')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from solo.app import create_app
from solo.cloud import CloudLLM
from solo.cloud_settings import CloudSettings
from solo.cloud_cost import CostLedger

WORK = ROOT / '.uitmp' / 'control'
PROFILE_DIR = WORK / 'profile'


def main():
    settings = CloudSettings(PROFILE_DIR / 'cloud-profiles.json')
    ledger = CostLedger(PROFILE_DIR / 'cloud-usage.sqlite3')
    llm = CloudLLM(settings, ledger)
    app = create_app(WORK / 'solo.sqlite3', llm, experimental=True)
    did = (WORK / 'draft-id.txt').read_text(encoding='utf-8').strip()
    import sqlite3
    before = sqlite3.connect(PROFILE_DIR / 'cloud-usage.sqlite3').execute(
        'select count(*), coalesce(sum(cost),0) from calls').fetchone()
    with app.state.preparation_pipeline_guard() if hasattr(app.state, 'preparation_pipeline_guard') else _null():
        value = app.state.preparation_pipeline.recheck_start(did)
    after = sqlite3.connect(PROFILE_DIR / 'cloud-usage.sqlite3').execute(
        'select count(*), coalesce(sum(cost),0) from calls').fetchone()
    print('calls before', before, 'after', after)
    print('preparation status', value.get('preparation', {}).get('status'))
    Path(WORK / 'recheck.json').write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')


class _null:
    def __enter__(self): return self
    def __exit__(self, *exc): return False


if __name__ == '__main__':
    main()
