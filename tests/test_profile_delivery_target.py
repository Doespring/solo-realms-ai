"""r209: ключ не уезжает на новый адрес, а адрес проверяется резолвом.

Аудит 03.11 показал две дыры одного узла: `POST /api/ai/profiles` менял
provider/base_url у профиля с сохранённым ключом и оставлял ключ в vault
( bearer уходил на чужой сервер), а проверка адреса смотрела на написание, а не
на резолв, поэтому `https://internal.example` проходил в приватную сеть.

Тесты резолва подменяют `solo.cloud_settings.resolve_host` — сеть в прогоне не
участвует. Настоящий путь проверяется отдельно (тест `test_default_resolver_*`).
"""
import socket
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.cloud_control import EnableCloud, enable
from solo.cloud_settings import (
    CloudProfile,
    CloudSettings,
    resolve_host,
    verify_public_address,
    verify_public_host,
)

from test_api_gm_hotfix import setup
from test_cloud import TestVault


def _enabled_profile(tmp_path):
    """Профиль RouterAI с ключом и подтверждённым лимитом — как у живого игрока."""
    llm, profile = setup(tmp_path)
    enable(
        llm.settings,
        llm.ledger,
        profile.id,
        EnableCloud(confirm=True, amount='2', max_request='1', currency='RUB'),
        llm.session,
    )
    assert llm.settings.profile(profile.id).enabled and llm.settings.vault.exists(profile.id)
    return llm, profile


def test_changing_base_url_disables_profile_and_drops_key(tmp_path, public_dns):
    llm, profile = _enabled_profile(tmp_path)
    with TestClient(create_app(tmp_path / 'solo.sqlite3', llm)) as client:
        raw = llm.settings.profile(profile.id).model_dump(mode='json')
        raw.update(provider='OpenAI-compatible', base_url='https://api.openai.com/v1')
        response = client.post('/api/ai/profiles', json=raw)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['disabled'] is True and body['key_present'] is False
        assert 'ключ удалён' in body['notice'] and 'Включите облачный AI заново' in body['notice']
        assert 'Свой адрес' in body['warning']

        saved = llm.settings.profile(profile.id)
        assert saved.enabled is False
        assert (saved.provider, saved.base_url) == ('OpenAI-compatible', 'https://api.openai.com/v1')
        assert not llm.settings.vault.exists(profile.id)

        # Включить заново без ключа нельзя, даже с подтверждением лимита.
        request = dict(amount='2', max_request='1', currency='RUB', confirm=True)
        assert client.post('/api/ai/profiles/' + profile.id + '/enable', json=request).status_code == 409
        # И «тихое» включение через сохранение формы тоже отклоняется.
        again = llm.settings.profile(profile.id).model_dump(mode='json')
        again['enabled'] = True
        assert client.post('/api/ai/profiles', json=again).status_code == 409

        # Новый ключ на новом адресе — снова рабочая процедура с предупреждением.
        assert client.put('/api/ai/profiles/' + profile.id + '/key', json={'api_key': 'synthetic-new-key'}).status_code == 200
        enabled = client.post('/api/ai/profiles/' + profile.id + '/enable', json=request)
        assert enabled.status_code == 200, enabled.text
        assert llm.settings.profile(profile.id).enabled


def test_changing_only_the_model_keeps_the_key_on_the_same_server(tmp_path, public_dns):
    llm, profile = _enabled_profile(tmp_path)
    with TestClient(create_app(tmp_path / 'solo.sqlite3', llm)) as client:
        raw = llm.settings.profile(profile.id).model_dump(mode='json')
        raw['narrator']['model'] = 'another-mock-model'
        response = client.post('/api/ai/profiles', json=raw)
        assert response.status_code == 200, response.text
        assert response.json()['disabled'] is False and response.json()['key_present'] is True
        assert llm.settings.profile(profile.id).enabled and llm.settings.vault.exists(profile.id)
        assert llm.settings.profile(profile.id).narrator.model == 'another-mock-model'


def test_delivery_target_ignores_request_only_parameters():
    profile = CloudProfile(provider='My Gateway', base_url='https://Provider.Example/v1')
    same_server = profile.model_copy(
        update={
            'narrator': profile.narrator.model_copy(update={'model': 'another', 'temperature': 1.1}),
            'provider': 'my gateway',
        }
    )
    assert profile.delivery_target() == same_server.delivery_target()
    for change in (
        {'provider': 'groq'},
        {'base_url': 'https://provider.example/v2'},
        {'base_url': 'https://other.example/v1'},
    ):
        assert profile.delivery_target() != profile.model_copy(update=change).delivery_target(), change


@pytest.mark.parametrize(
    'url',
    [
        'https://10.0.0.15/v1',
        'https://172.16.0.5/v1',
        'https://172.31.255.1/v1',
        'https://192.168.1.10/v1',
        'https://169.254.169.254/v1',
        'https://100.64.0.1/v1',
        'https://127.0.0.1/v1',
        'https://[::1]/v1',
        'https://[fd00::1]/v1',
        'https://[fe80::1]/v1',
    ],
)
def test_private_and_service_addresses_are_refused(url):
    with pytest.raises(ValueError):
        CloudProfile(base_url=url)


def test_public_literal_address_is_accepted():
    assert CloudProfile(base_url='https://8.8.8.8/v1').base_url == 'https://8.8.8.8/v1'


def test_name_is_judged_by_resolution_not_by_spelling():
    assert verify_public_host('api.openai.com', resolve=lambda host: ['93.184.216.34']) == ['93.184.216.34']
    with pytest.raises(ValueError, match='приватную'):
        verify_public_host('internal.example', resolve=lambda host: ['10.0.0.5'])
    # Один публичный и один приватный адрес в ответе — это попытка обхода.
    with pytest.raises(ValueError, match='приватную'):
        verify_public_host('mixed.example', resolve=lambda host: ['93.184.216.34', '10.0.0.5'])

    def nxdomain(host):
        raise socket.gaierror(-2, 'Name or service not known')

    with pytest.raises(ValueError, match='не разрешается'):
        verify_public_host('internal.example', resolve=nxdomain)
    with pytest.raises(ValueError, match='не разрешается'):
        verify_public_host('empty.example', resolve=lambda host: [])
    assert verify_public_address('https://api.openai.com/v1', resolve=lambda host: ['93.184.216.34']) == ['93.184.216.34']


def test_name_pointing_into_private_network_is_refused_before_save(tmp_path, monkeypatch):
    from solo import cloud_settings

    llm, profile = _enabled_profile(tmp_path)
    monkeypatch.setattr(cloud_settings, 'resolve_host', lambda host: ['10.0.0.5'])
    with TestClient(create_app(tmp_path / 'solo.sqlite3', llm)) as client:
        raw = llm.settings.profile(profile.id).model_dump(mode='json')
        raw.update(provider='OpenAI-compatible', base_url='https://internal.example/v1')
        response = client.post('/api/ai/profiles', json=raw)
        assert response.status_code == 409, response.text
        assert 'приватную' in response.text
        saved = llm.settings.profile(profile.id)
        assert saved.base_url != 'https://internal.example/v1'
        assert saved.enabled and llm.settings.vault.exists(profile.id)


def test_unresolvable_name_is_refused_before_save(tmp_path, monkeypatch):
    from solo import cloud_settings

    llm, profile = _enabled_profile(tmp_path)

    def nxdomain(host):
        raise socket.gaierror(-2, 'Name or service not known')

    monkeypatch.setattr(cloud_settings, 'resolve_host', nxdomain)
    with TestClient(create_app(tmp_path / 'solo.sqlite3', llm)) as client:
        raw = llm.settings.profile(profile.id).model_dump(mode='json')
        raw.update(provider='OpenAI-compatible', base_url='https://internal.example/v1')
        response = client.post('/api/ai/profiles', json=raw)
        assert response.status_code == 409, response.text
        assert 'не разрешается' in response.text
        assert llm.settings.profile(profile.id).enabled


def test_saved_profile_is_read_without_network(tmp_path, monkeypatch):
    """Загрузка сохранённых настроек не зависит от DNS: иначе офлайн-старт падал бы."""
    from solo import cloud_settings

    profile = CloudProfile(base_url='https://provider.example/v1')
    settings = CloudSettings(tmp_path / 'profiles.json', TestVault())
    settings.save(profile)

    def nxdomain(host):
        raise socket.gaierror(-2, 'Name or service not known')

    monkeypatch.setattr(cloud_settings, 'resolve_host', nxdomain)
    loaded = CloudSettings(tmp_path / 'profiles.json', TestVault())
    assert loaded.profile(profile.id).base_url == 'https://provider.example/v1'


def test_default_resolver_asks_dns(monkeypatch):
    calls = []

    def fake_getaddrinfo(host, port, **kwargs):
        calls.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))]

    monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)
    assert resolve_host('api.openai.com') == ['93.184.216.34']
    assert calls == [('api.openai.com', 443)]


def test_budget_limit_still_cannot_be_raised_by_saving_the_form(tmp_path, public_dns):
    llm, profile = _enabled_profile(tmp_path)
    with TestClient(create_app(tmp_path / 'solo.sqlite3', llm)) as client:
        raw = llm.settings.profile(profile.id).model_dump(mode='json')
        raw['total_budget'] = '99'
        assert client.post('/api/ai/profiles', json=raw).status_code == 409
        assert llm.settings.profile(profile.id).total_budget == Decimal('2')
