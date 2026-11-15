from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import re
import threading

import pytest
import requests

from lab import Lab, MESSAGE, Provider, create_app, mask


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Network forbidden in tests')
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)


@pytest.fixture
def setup(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    provider = Provider()
    lab = Lab(tmp_path / 'test.sqlite3', provider, clock=lambda: now[0])
    return lab, now


def test_sixty_seconds_and_two_call_cap(setup):
    lab, now = setup
    lab.create()
    assert lab.tick()
    now[0] += timedelta(seconds=59.999)
    assert not lab.tick()
    now[0] += timedelta(seconds=.001)
    assert lab.tick()
    now[0] += timedelta(days=1)
    assert not lab.tick()
    assert [c['slot'] for c in lab.snapshot()[0]['calls']] == [1, 2]
    assert all(c['state'] == 'simulated' for c in lab.snapshot()[0]['calls'])


def test_ack_and_resolution_are_separate_and_idempotent(setup):
    lab, now = setup
    ident = lab.create()
    lab.tick()
    assert lab.action(ident, 'resolve') == 409
    assert lab.action(ident, 'ack') == 303
    ack = lab.snapshot()[0]['acknowledged_utc']
    now[0] += timedelta(seconds=60)
    assert not lab.tick()
    lab.action(ident, 'ack')
    assert lab.snapshot()[0]['acknowledged_utc'] == ack
    assert lab.snapshot()[0]['resolved_utc'] is None
    assert lab.action(ident, 'resolve') == 303
    assert lab.snapshot()[0]['resolved_utc']


def test_double_clicks_and_parallel_workers(setup):
    lab, now = setup
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: lab.create(), range(12)))
    assert len(set(ids)) == 1
    lab.tick()
    now[0] += timedelta(seconds=60)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: lab.tick(), range(12)))
    assert sum(outcomes) == 1
    assert len(lab.snapshot()[0]['calls']) == 2


def test_ack_wins_before_claim(setup):
    lab, now = setup
    ident = lab.create()
    lab.tick()
    now[0] += timedelta(seconds=60)
    lab.action(ident, 'ack')
    assert lab.claim() is None


def test_claim_wins_ack_during_provider_request(setup):
    lab, now = setup
    ident = lab.create()
    lab.tick()
    now[0] += timedelta(seconds=60)
    entered, release = threading.Event(), threading.Event()
    def blocked_send(slot):
        assert slot == 2
        entered.set()
        assert release.wait(5)
        return 'accepted', 'CA' + '0' * 32, 'Receipt unconfirmed'
    lab.provider.send = blocked_send
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(lab.tick)
        assert entered.wait(5)
        assert lab.action(ident, 'ack') == 303
        release.set()
        assert task.result(timeout=5)
    assert len(lab.snapshot()[0]['calls']) == 2
    assert not lab.tick()


def test_restart_reserved_slot_never_replayed_but_escalation_resumes(setup):
    lab, now = setup
    lab.create()
    assert lab.claim()[1] == 1  # Simulate crash before recording result.
    resumed = Lab(lab.database, lab.provider, clock=lab.clock)
    assert not resumed.tick()
    now[0] += timedelta(seconds=90)
    assert resumed.tick()
    assert not resumed.tick()
    assert resumed.snapshot()[0]['calls'][0]['state'] == 'reserved'


def test_mode_or_destination_change_cannot_dispatch_old_alerts(setup):
    lab, now = setup
    lab.create()
    lab.provider.key = 'different-configuration'
    now[0] += timedelta(seconds=90)
    assert not lab.tick()
    assert not lab.snapshot()[0]['config_matches']


@pytest.fixture
def live_provider(monkeypatch):
    # These are synthetic placeholders, never credentials or a dialable test target.
    monkeypatch.setenv('TWILIO_ACCOUNT_SID', 'AC' + '0' * 32)
    monkeypatch.setenv('TWILIO_AUTH_TOKEN', 'synthetic-secret')
    monkeypatch.setenv('TWILIO_FROM_NUMBER', '+15005550006')
    monkeypatch.setenv('ALERT_TO_NUMBER', '+212600000000')
    monkeypatch.delenv('ALERT_BACKUP_NUMBER', raising=False)
    return Provider(live=True)


def mock_response(monkeypatch, status=201, body=None, error=None):
    post = Mock(side_effect=error)
    if error is None:
        post.return_value = Mock(status_code=status, json=Mock(return_value=body))
    # Replace post above the network-denial guard; no socket can be reached.
    monkeypatch.setattr(requests.Session, 'post', post)
    return post


def test_inline_twiml_and_accepted_is_not_received(monkeypatch, live_provider):
    post = mock_response(monkeypatch, body={'sid': 'CA' + '1' * 32})
    state, sid, detail = live_provider.send(2)
    assert state == 'accepted' and 'unconfirmed' in detail
    kwargs = post.call_args.kwargs
    assert 'Backup engineer escalation. ' + MESSAGE in kwargs['data']['Twiml']
    assert 'Url' not in kwargs['data'] and 'StatusCallback' not in kwargs['data']
    assert kwargs['allow_redirects'] is False
    live_provider.send(1)
    assert 'Backup engineer' not in post.call_args.kwargs['data']['Twiml']


@pytest.mark.parametrize('status,body,expected', [
    (400, {'code': 21215, 'message': 'secret +212600000000'}, 'failed'),
    (401, {'code': 20003}, 'failed'),
    (503, {}, 'unknown'),
    (201, {}, 'unknown'),
])
def test_failures_are_visible_and_sanitized(monkeypatch, live_provider, status, body, expected):
    post = mock_response(monkeypatch, status, body)
    state, _, detail = live_provider.send(1)
    assert state == expected
    assert 'secret' not in detail and '+212' not in detail
    assert post.call_count == 1


def test_timeout_not_retried(monkeypatch, live_provider, tmp_path):
    post = mock_response(monkeypatch, error=requests.Timeout('secret'))
    lab = Lab(tmp_path / 'timeout.sqlite3', live_provider)
    lab.create()
    lab.tick()
    assert not lab.tick()
    assert post.call_count == 1
    assert lab.snapshot()[0]['calls'][0]['state'] == 'unknown'


def test_web_csrf_host_and_separate_actions(setup):
    lab, _ = setup
    client = create_app(lab).test_client()
    base = 'http://127.0.0.1:5000'
    page = client.get('/', base_url=base)
    token = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    assert client.post('/generate', base_url=base).status_code == 403
    assert client.get('/', base_url='http://attacker.example:5000').status_code == 403
    assert client.get('/', base_url=base, environ_overrides={'REMOTE_ADDR': '192.168.1.5'}).status_code == 403
    assert client.post('/generate', data={'csrf': token}, base_url=base).status_code == 303
    assert client.get('/alerts/1/ack', base_url=base).status_code == 405
    assert client.post('/alerts/1/resolve', data={'csrf': token}, base_url=base).status_code == 409
    assert client.post('/alerts/1/ack', data={'csrf': token}, base_url=base).status_code == 303
    assert client.post('/alerts/1/resolve', data={'csrf': token}, base_url=base).status_code == 303


def test_live_web_requires_explicit_consent(live_provider, tmp_path):
    client = create_app(Lab(tmp_path / 'live.sqlite3', live_provider)).test_client()
    base = 'http://127.0.0.1:5000'
    token = re.search(r'name="csrf" value="([^"]+)"', client.get('/', base_url=base).text)[1]
    assert client.post('/generate', data={'csrf': token}, base_url=base).status_code == 400
    assert client.post('/generate', data={'csrf': token, 'live_consent': 'yes'}, base_url=base).status_code == 303


def test_mask_and_dry_run_never_contacts_provider():
    assert mask('+212600000000') == '***00'
    assert mask('placeholder') == '(not configured)'
    assert Provider().send(1)[0] == 'simulated'


def test_two_destinations_and_configuration_isolation(monkeypatch, live_provider):
    original_key = live_provider.key
    monkeypatch.setenv('ALERT_BACKUP_NUMBER', '+212700000000')
    provider = Provider(live=True)
    assert provider.key != original_key
    post = mock_response(monkeypatch, body={'sid': 'CA' + '1' * 32})
    provider.send(1)
    assert post.call_args.kwargs['data']['To'] == '+212600000000'
    provider.send(2)
    assert post.call_args.kwargs['data']['To'] == '+212700000000'


def test_backup_defaults_to_primary(live_provider):
    assert live_provider.backup == live_provider.target
