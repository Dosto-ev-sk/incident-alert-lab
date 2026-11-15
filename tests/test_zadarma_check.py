import base64
import hashlib
import hmac
from unittest.mock import Mock

import pytest
import requests

from zadarma_check import authorization, read_account


def test_signature_matches_documented_algorithm_and_sorted_parameters():
    query = 'a=one+two&z=3'
    message = '/v1/info/price/' + query + hashlib.md5(query.encode()).hexdigest()
    expected = base64.b64encode(hmac.new(b'synthetic-secret', message.encode(), hashlib.sha1).hexdigest().encode()).decode()
    assert authorization('synthetic-key', 'synthetic-secret', '/v1/info/price/', {'z': '3', 'a': 'one two'}) == 'synthetic-key:' + expected


def test_checker_refuses_call_originating_endpoint():
    with pytest.raises(ValueError):
        read_account('/v1/request/callback/', {'to': 'never-dial'})


def test_checker_does_not_log_credentials_or_raw_response(monkeypatch, capsys):
    monkeypatch.setenv('ZADARMA_KEY', 'synthetic-key')
    monkeypatch.setenv('ZADARMA_SECRET', 'synthetic-secret')
    post = Mock(return_value=Mock(status_code=401, text='synthetic-secret'))
    monkeypatch.setattr(requests.Session, 'get', post)
    assert read_account('/v1/info/balance/') is None
    output = capsys.readouterr().out
    assert '401' in output
    assert 'synthetic-key' not in output and 'synthetic-secret' not in output
    assert post.call_args.kwargs['allow_redirects'] is False
