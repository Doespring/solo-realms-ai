"""Opt-in frozen-hook seams; API tests use a fake renderer, not native-window evidence."""
import json,sys
import pytest
from fastapi.testclient import TestClient
from solo.desktop_observations import validate_pdf_smoke_profile,pdf_prepared_smoke,PDF_SMOKE_MARKER


def test_pdf_smoke_refuses_normal_nonempty_or_unmarked_profile(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'appdata'))
    from solo.prototype_profile import default_profile
    with pytest.raises(ValueError,match='normal profile'):
        validate_pdf_smoke_profile(default_profile(True),'prepare')
    profile=tmp_path/'own';profile.mkdir();(profile/'untouched.txt').write_text('canary')
    with pytest.raises(ValueError,match='new empty'):
        validate_pdf_smoke_profile(profile,'prepare')
    with pytest.raises(ValueError,match='completed prepare marker'):
        validate_pdf_smoke_profile(profile,'continue')
    (profile/PDF_SMOKE_MARKER).write_text('{}')
    with pytest.raises(ValueError,match='Invalid'):
        validate_pdf_smoke_profile(profile,'continue')
    assert (profile/'untouched.txt').read_text()=='canary'


@pytest.mark.parametrize('flags',[[],['--smoke-test','unused.json'],
    ['--smoke-test','unused.json','--offline-mock']])
def test_pdf_fixture_cli_requires_all_explicit_qa_flags(tmp_path,monkeypatch,flags):
    import desktop_main
    profile=tmp_path/'never-created'
    monkeypatch.setattr(sys,'argv',['desktop_main.py','--smoke-pdf-phase','prepare','--data-dir',str(profile),*flags])
    with pytest.raises(SystemExit) as stopped:desktop_main.main()
    assert stopped.value.code==2 and not profile.exists()


class RendererFixture:
    def evaluate_js(self,expression):
        return json.dumps(dict(title='Test renderer fixture',creation=True,fonts=True))


def test_registered_pdf_hook_and_recreated_app_continue_without_repreparing(tmp_path):
    from solo.app import create_app
    from solo.prototype_profile import prepare_profile
    from scripts.smoke_fixture import make_pdf_world_smoke_llm
    profile=validate_pdf_smoke_profile(tmp_path/'qa','prepare')
    prepare_profile(profile,offline=True)
    with TestClient(create_app(profile/'solo.sqlite3',make_pdf_world_smoke_llm(profile,smoke_test=True,phase='prepare'),experimental=True,offline_mock=True)) as client:
        prepared=pdf_prepared_smoke(client,RendererFixture(),client.app,profile,'prepare')
        assert prepared['synthetic_pdf'] and prepared['real_pdf_prepared']
        assert prepared['preparation_mock_requests']==1 and not prepared['new_process_continued']
    validate_pdf_smoke_profile(profile,'continue')
    with TestClient(create_app(profile/'solo.sqlite3',make_pdf_world_smoke_llm(profile,smoke_test=True,phase='continue'),experimental=True,offline_mock=True)) as client:
        continued=pdf_prepared_smoke(client,RendererFixture(),client.app,profile,'continue')
        assert continued['preparation_mock_requests']==0 and continued['version']==prepared['version']+1
    # This test recreates the app in-process; the release check must launch EXE twice.
