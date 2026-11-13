"""Хранилище ключей API вне Windows: явный выбор бэкенда, шифрованный файл, честный отказ.

Синтетические ключи только вида ``test-key-not-real``: в репозитории нет настоящих секретов.
"""
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.cloud_settings import (CloudProfile, CloudSettings, FileKeyVault, KeyringVault,
                                 KeyVaultError, KeyVaultUnavailable, UnavailableVault,
                                 VAULT_ENV, WindowsKeyVault, default_key_file, select_vault)

SECRET = 'test-key-not-real'


def scan_plaintext(root, needle=SECRET):
    """Побайтовый поиск открытого ключа по всему каталогу."""
    root = Path(root)
    return [str(p) for p in root.rglob('*') if p.is_file() and needle.encode() in p.read_bytes()]


def private(tmp_path):
    """Каталог шифротекста и файл ключа — в отдельных каталогах, оба вне профиля."""
    return tmp_path / 'cloud-secrets', tmp_path / 'keyvault' / 'vault.key'


def mode(path):
    return stat.S_IMODE(Path(path).stat().st_mode)


# --- file backend -----------------------------------------------------------

def test_file_vault_roundtrip_and_delete(tmp_path):
    secrets, key = private(tmp_path)
    vault = FileKeyVault(secrets, key)
    profile = str(CloudProfile(base_url='https://provider.example/v1').id)
    assert vault.get(profile) is None and not vault.exists(profile)
    vault.set(profile, SECRET)
    assert vault.exists(profile) and vault.get(profile) == SECRET
    vault.set(profile, 'test-key-not-real-2')
    assert vault.get(profile) == 'test-key-not-real-2'
    vault.delete(profile)
    assert not vault.exists(profile) and vault.get(profile) is None


def test_open_key_never_appears_in_files(tmp_path):
    secrets, key = private(tmp_path)
    vault = FileKeyVault(secrets, key)
    profile = str(CloudProfile(base_url='https://provider.example/v1').id)
    vault.set(profile, SECRET)
    assert vault.get(profile) == SECRET
    assert scan_plaintext(tmp_path) == [], 'открытый ключ нашёлся в файлах'
    # шифротекст действительно шифротекст: байты ключа и токена не совпадают
    assert SECRET.encode() not in vault.file(profile).read_bytes()


@pytest.mark.skipif(os.name == 'nt', reason='права POSIX проверяются только на POSIX')
def test_private_files_are_0600(tmp_path):
    secrets, key = private(tmp_path)
    vault = FileKeyVault(secrets, key)
    profile = str(CloudProfile(base_url='https://provider.example/v1').id)
    vault.set(profile, SECRET)
    assert mode(key) == 0o600, f'файл ключа шифрования {oct(mode(key))}'
    assert mode(vault.file(profile)) == 0o600, f'шифротекст {oct(mode(vault.file(profile)))}'


def test_key_file_is_not_next_to_the_profile_data(tmp_path):
    vault = FileKeyVault(*private(tmp_path))
    assert vault.key_path.parent != vault.path and vault.path not in vault.key_path.parents
    with pytest.raises(ValueError, match='каталоге с зашифрованными ключами'):
        FileKeyVault(vault.path, vault.path / 'vault.key')


def test_default_key_file_is_outside_the_repository():
    repo = Path(__file__).resolve().parents[1]
    key = default_key_file()
    assert repo not in key.resolve().parents, f'ключ шифрования попал в репозиторий: {key}'
    assert '.local/share/solo-realms' in str(key) or 'AppData' in str(key)


def test_ciphertext_is_useless_without_the_key_file(tmp_path):
    secrets, key = private(tmp_path)
    vault = FileKeyVault(secrets, key)
    profile = str(CloudProfile(base_url='https://provider.example/v1').id)
    vault.set(profile, SECRET)
    other_key = tmp_path / 'other' / 'vault.key'
    stranger = FileKeyVault(secrets, other_key)
    with pytest.raises(KeyVaultError, match='не расшифровывается'):
        stranger.get(profile)


def test_corrupted_ciphertext_is_reported_in_russian(tmp_path):
    secrets, key = private(tmp_path)
    vault = FileKeyVault(secrets, key)
    profile = str(CloudProfile(base_url='https://provider.example/v1').id)
    vault.set(profile, SECRET)
    vault.file(profile).write_bytes(b'not-a-fernet-token')
    with pytest.raises(KeyVaultError, match='не расшифровывается'):
        vault.get(profile)


def test_invalid_key_is_refused_by_file_backend(tmp_path):
    vault = FileKeyVault(*private(tmp_path))
    profile = str(CloudProfile(base_url='https://provider.example/v1').id)
    for bad in ('', 'a' * 4097, 'line\nbreak', None):
        with pytest.raises(ValueError, match='Некорректный API-ключ'):
            vault.set(profile, bad)
    assert not vault.exists(profile)


# --- выбор бэкенда ----------------------------------------------------------

class MemoryKeyring:
    """Подмена живого SecretService: проверяется адаптер, а не системный демон.

    ``priority=-1`` обязателен: подкласс KeyringBackend регистрируется в keyring
    на весь процесс, и с высоким приоритетом его выбрала бы ленивая автодискавери
    любого следующего теста.
    """

    def __init__(self):
        import keyring.backend
        outer = self

        class Backend(keyring.backend.KeyringBackend):
            priority = -1

            def get_password(self, service, username):
                return outer.data.get((service, username))

            def set_password(self, service, username, password):
                outer.data[(service, username)] = password

            def delete_password(self, service, username):
                import keyring.errors
                if (service, username) not in outer.data:
                    raise keyring.errors.PasswordDeleteError('no such password')
                del outer.data[(service, username)]

        self.backend = Backend()
        self.data = {}


@pytest.fixture
def memory_keyring():
    keyring = pytest.importorskip('keyring')
    core = pytest.importorskip('keyring.core')
    previous = core._keyring_backend  # точное состояние, включая «ещё не выбрано»
    store = MemoryKeyring()
    keyring.set_keyring(store.backend)
    try:
        yield store
    finally:
        core._keyring_backend = previous


def test_auto_selection_falls_back_to_file_without_keyring_backend(tmp_path, monkeypatch):
    secrets, key = private(tmp_path)
    monkeypatch.delenv(VAULT_ENV, raising=False)
    monkeypatch.setattr(KeyringVault, 'available', classmethod(lambda cls: False))
    vault = select_vault(secrets, key_path=key)
    assert vault.backend == 'file' and isinstance(vault, FileKeyVault)


def test_auto_selection_uses_keyring_when_backend_is_live(tmp_path, monkeypatch, memory_keyring):
    secrets, key = private(tmp_path)
    monkeypatch.delenv(VAULT_ENV, raising=False)
    assert KeyringVault.available() is True
    vault = select_vault(secrets, key_path=key)
    assert vault.backend == 'keyring'
    profile = str(CloudProfile(base_url='https://provider.example/v1').id)
    vault.set(profile, SECRET)
    assert vault.get(profile) == SECRET and memory_keyring.data[(vault.service, profile)] == SECRET
    vault.delete(profile)
    assert vault.get(profile) is None
    assert not secrets.exists(), 'keyring-путь не должен трогать каталог профиля'


def test_keyring_vault_without_backend_reports_in_russian():
    pytest.importorskip('keyring')
    if KeyringVault.available():
        pytest.skip('на машине есть живой SecretService: ветка отказа не воспроизводится')
    vault = KeyringVault()
    with pytest.raises(KeyVaultUnavailable, match='Системное хранилище ключей недоступно'):
        vault.set(str(CloudProfile(base_url='https://provider.example/v1').id), SECRET)
    assert vault.exists(str(CloudProfile(base_url='https://provider.example/v1').id)) is False


def test_plaintext_keyring_backend_is_not_treated_as_storage(tmp_path, monkeypatch):
    keyring = pytest.importorskip('keyring')
    core = pytest.importorskip('keyring.core')
    from keyring.backend import KeyringBackend

    class PlaintextKeyring(KeyringBackend):
        priority = -1

        def get_password(self, service, username):
            return None

        def set_password(self, service, username, password):
            return None

        def delete_password(self, service, username):
            return None

    previous = core._keyring_backend
    keyring.set_keyring(PlaintextKeyring())
    try:
        assert KeyringVault.available() is False, 'PlaintextKeyring выдан за живое хранилище'
        with pytest.raises(KeyVaultUnavailable, match='открытым текстом'):
            KeyringVault().set(str(CloudProfile(base_url='https://provider.example/v1').id), SECRET)
        secrets, key = private(tmp_path)
        monkeypatch.delenv(VAULT_ENV, raising=False)
        assert select_vault(secrets, key_path=key).backend == 'file'
    finally:
        core._keyring_backend = previous


def test_explicit_choice_argument_env_and_platform(tmp_path, monkeypatch):
    secrets, key = private(tmp_path)
    monkeypatch.delenv(VAULT_ENV, raising=False)
    assert select_vault(secrets, backend='file', key_path=key).backend == 'file'
    monkeypatch.setenv(VAULT_ENV, 'file')
    assert select_vault(secrets, key_path=key).backend == 'file'
    monkeypatch.setenv(VAULT_ENV, 'dpapi')
    assert select_vault(secrets, key_path=key).backend == 'dpapi'
    assert isinstance(select_vault(secrets, key_path=key), WindowsKeyVault)
    monkeypatch.setattr(os, 'name', 'nt')
    monkeypatch.delenv(VAULT_ENV, raising=False)
    assert select_vault(secrets, key_path=key).backend == 'dpapi'


def test_unknown_choice_is_refused(tmp_path):
    secrets, key = private(tmp_path)
    with pytest.raises(ValueError, match='Неизвестное хранилище ключей'):
        select_vault(secrets, backend='dpl0cal', key_path=key)


def test_no_backend_available_gives_russian_refusal_and_no_file(tmp_path, monkeypatch):
    blocker = tmp_path / 'not-a-directory'
    blocker.write_text('файл вместо каталога')
    secrets = tmp_path / 'cloud-secrets'
    key_inside_blocker = blocker / 'vault.key'
    monkeypatch.delenv(VAULT_ENV, raising=False)
    monkeypatch.setattr(KeyringVault, 'available', classmethod(lambda cls: False))
    vault = select_vault(secrets, key_path=key_inside_blocker)
    assert vault.backend == 'unavailable' and isinstance(vault, UnavailableVault)
    profile = str(CloudProfile(base_url='https://provider.example/v1').id)
    assert vault.exists(profile) is False and vault.get(profile) is None
    with pytest.raises(KeyVaultUnavailable, match='Хранилище ключей API недоступно'):
        vault.set(profile, SECRET)
    assert scan_plaintext(tmp_path) == []


def test_windows_key_vault_still_refuses_outside_windows(tmp_path):
    if os.name == 'nt':
        pytest.skip('Windows DPAPI здесь доступен; ветка отказа только для POSIX')
    vault = WindowsKeyVault(tmp_path / 'cloud-secrets')
    profile = str(CloudProfile(base_url='https://provider.example/v1').id)
    with pytest.raises(RuntimeError, match='Windows DPAPI unavailable; no plaintext secret fallback'):
        vault.set(profile, SECRET)
    assert not any(tmp_path.rglob('*.dpapi')), 'на Linux появился DPAPI-файл'


@pytest.mark.skipif(os.name != 'nt', reason='Windows CurrentUser DPAPI')
def test_windows_roundtrip_through_selection(tmp_path):
    secrets, key = private(tmp_path)
    vault = select_vault(secrets, key_path=key)
    assert vault.backend == 'dpapi'
    profile = str(CloudProfile(base_url='https://provider.example/v1').id)
    vault.set(profile, SECRET)
    assert vault.get(profile) == SECRET and SECRET.encode() not in vault.file(profile).read_bytes()
    vault.delete(profile)
    assert not vault.exists(profile)


# --- приложение целиком -----------------------------------------------------

@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Данные приложения и каталог ключа — в tmp, чтобы тест не трогал домашний каталог."""
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg'))
    monkeypatch.delenv(VAULT_ENV, raising=False)
    return tmp_path


def test_selected_backend_is_visible_in_profiles_api(app_env):
    app = create_app(app_env / 'api.sqlite3')
    with TestClient(app) as client:
        payload = client.get('/api/ai/profiles').json()
    backend = app.state.cloud_settings.vault.backend
    assert payload['vault_backend'] == backend
    assert backend in ('file', 'keyring', 'unavailable'), backend


def test_api_saves_key_on_linux_without_plaintext(app_env,public_dns):
    app = create_app(app_env / 'api.sqlite3')
    if app.state.cloud_settings.vault.backend == 'unavailable':
        pytest.skip('на этой машине не нашлось хранилища: ветка отказа проверена отдельно')
    profile = CloudProfile(base_url='https://provider.example/v1')
    with TestClient(app) as client:
        assert client.post('/api/ai/profiles', json=profile.model_dump(mode='json')).status_code == 200
        saved = client.put('/api/ai/profiles/' + profile.id + '/key', json={'api_key': SECRET})
        assert saved.status_code == 200 and SECRET not in saved.text
        listed = client.get('/api/ai/profiles')
        assert SECRET not in listed.text
        assert [p['key_present'] for p in listed.json()['profiles']] == [True]
        assert client.delete('/api/ai/profiles/' + profile.id + '/key').status_code == 200
        assert [p['key_present'] for p in client.get('/api/ai/profiles').json()['profiles']] == [False]
    assert scan_plaintext(app_env) == [], 'открытый ключ остался в файлах приложения'


def test_key_save_refusal_is_http_409_in_russian(app_env, monkeypatch,public_dns):
    import solo.app as app_module
    monkeypatch.setattr(app_module, 'select_vault', lambda *a, **k: UnavailableVault('нет ни keyring, ни прав на файл'))
    app = create_app(app_env / 'api.sqlite3')
    profile = CloudProfile(base_url='https://provider.example/v1')
    with TestClient(app) as client:
        assert client.post('/api/ai/profiles', json=profile.model_dump(mode='json')).status_code == 200
        refused = client.put('/api/ai/profiles/' + profile.id + '/key', json={'api_key': SECRET})
        listed = client.get('/api/ai/profiles')
    assert refused.status_code == 409, refused.text
    assert 'Хранилище ключей API недоступно' in refused.json()['detail']
    assert SECRET not in refused.text and SECRET not in listed.text
    assert listed.json()['vault_backend'] == 'unavailable'
    assert scan_plaintext(app_env) == []


def test_cloud_settings_selects_a_vault_by_default(app_env):
    settings = CloudSettings(app_env / 'cloud-profiles.json')
    assert settings.vault.backend in ('file', 'keyring', 'unavailable')
    assert settings.public()['vault_backend'] == settings.vault.backend
