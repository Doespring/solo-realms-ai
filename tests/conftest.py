import os
os.environ['SOLO_TEST']='1'

import pytest


@pytest.fixture(autouse=True)
def _restore_engine_lib_loader():
    """Restore the process-wide asset loader after every test.

    ``configure_lib_loader`` / ``set_lib_loader_for_tests`` install a loader
    for the WHOLE process, not for one test. A file that installed its own
    corpus and left it in place made the next file resolve against the wrong
    data — 117 failures of the form ``unknown background: 'soldier'`` in a
    full run, while every one of those files passed on its own. Restoring the
    lazy bundled default here keeps files independent without hiding anything:
    a test that leaks state fails the same way it would in isolation.

    Nothing to do if the engine is not importable in this environment.
    """
    try:
        from dnd5e_engine.lib_loader import configure_lib_loader
        from dnd5e_srd_data.loader import BundledAssetLoader
    except Exception:
        yield
        return
    try:
        yield
    finally:
        configure_lib_loader(BundledAssetLoader())


PUBLIC_TEST_ADDRESS = '93.184.216.34'  # публичный адрес из документации IANA
_DNS_FIXTURE_HOSTS = {'provider.example', 'example.test', 'a.example', 'b.example'}


@pytest.fixture
def public_dns(monkeypatch):
    """Имена из фикстур резолвятся в публичный адрес: проверка адреса не ходит в сеть.

    Подстановка точечная: неизвестное имя уходит в настоящий резолв, поэтому
    тест на отказ приватного/неразрешимого адреса эта фикстура не «озеленит».
    """
    from solo import cloud_settings

    real_resolve = cloud_settings.resolve_host

    def resolve(host, *args, **kwargs):
        if host.lower() in _DNS_FIXTURE_HOSTS:
            return [PUBLIC_TEST_ADDRESS]
        return real_resolve(host, *args, **kwargs)

    monkeypatch.setattr(cloud_settings, 'resolve_host', resolve)
    return PUBLIC_TEST_ADDRESS
