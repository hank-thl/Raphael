import hashlib
import json
import math
from unittest.mock import Mock

import httpx
import pytest

from app.core.config import Settings
from app.services.embeddings import (
    DIMENSIONS, MAX_INPUT_BYTES, MAX_RESPONSE_BYTES, MODEL, PROFILE,
    EmbeddingError, GeminiEmbedder, normalize,
)
from scripts import try_embeddings


def response(values=None):
    return {'embedding': {'values': values if values is not None else [3, 4] + [0]*(DIMENSIONS-2)}}


@pytest.mark.parametrize('kind,task', [('document', 'RETRIEVAL_DOCUMENT'), ('query', 'RETRIEVAL_QUERY')])
def test_fixed_profile_preserves_text_and_uses_only_expected_fields(kind, task, monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://private-proxy.invalid:123')
    calls = []
    original = '  繁體中文 日本語 English ภาษาไทย\n保留原文  '
    def handler(request):
        calls.append(request)
        assert str(request.url) == f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:embedContent'
        assert request.headers['x-goog-api-key'] == 'secret-key'
        assert 'secret-key' not in str(request.url)
        assert json.loads(request.content) == {
            'model': f'models/{MODEL}', 'content': {'parts': [{'text': original}]},
            'taskType': task, 'outputDimensionality': 768}
        return httpx.Response(200, json=response())
    client = GeminiEmbedder('secret-key', transport=httpx.MockTransport(handler))
    result = getattr(client, kind)(original)
    assert len(calls) == 1 and len(result.vector) == DIMENSIONS
    assert result.vector[:2] == pytest.approx((0.6, 0.8))
    assert math.hypot(*result.vector) == pytest.approx(1)
    assert result.text_hash == hashlib.sha256(original.encode()).hexdigest()
    assert result.profile == PROFILE and result.task == task
    assert 'secret-key' not in repr(client)
    assert 'vector=' not in repr(result)


@pytest.mark.parametrize('text,reason', [(None, 'invalid_input'), (1, 'invalid_input'),
    ('', 'invalid_input'), (' \n', 'invalid_input'), ('\ud800', 'invalid_input'),
    ('x'*(MAX_INPUT_BYTES+1), 'input_limit'), ('中'*(MAX_INPUT_BYTES//3+1), 'input_limit')])
def test_invalid_or_oversized_input_never_calls_provider(text, reason):
    handler = Mock()
    client = GeminiEmbedder('secret', transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError) as error: client.document(text)
    assert error.value.reason == reason
    handler.assert_not_called()


def test_exact_byte_budget_is_sent_in_full_not_sliced():
    text = '中'*(MAX_INPUT_BYTES//3)
    def handler(request):
        assert json.loads(request.content)['content']['parts'][0]['text'] == text
        return httpx.Response(200, json=response())
    GeminiEmbedder('key', transport=httpx.MockTransport(handler)).document(text)


@pytest.mark.parametrize('key', ['', '  ', None])
def test_missing_key_never_calls_provider(key):
    handler = Mock()
    with pytest.raises(EmbeddingError, match='not_configured'):
        GeminiEmbedder(key, transport=httpx.MockTransport(handler)).query('query')
    handler.assert_not_called()


@pytest.mark.parametrize('values', [[], [0]*DIMENSIONS, [1]*(DIMENSIONS-1), [1]*(DIMENSIONS+1),
    [True]*DIMENSIONS, ['1']*DIMENSIONS, [None]*DIMENSIONS,
    [float('nan')]*DIMENSIONS, [float('inf')]*DIMENSIONS, [10**500]*DIMENSIONS, {'values': []}])
def test_vector_dimensions_types_and_finiteness(values):
    with pytest.raises(EmbeddingError, match='invalid_vector'): normalize(values)


@pytest.mark.parametrize('magnitude', [1e308, 1e-320])
def test_normalization_handles_extreme_finite_components(magnitude):
    result = normalize([magnitude]*DIMENSIONS)
    assert math.hypot(*result) == pytest.approx(1)
    assert all(math.isfinite(v) for v in result)


@pytest.mark.parametrize('body', ['not-json private-text', '[]', '{}', '{"embedding":null}',
    '{"embedding":{"values":[],"values":[]}}', '{"embedding":{"values":[NaN]}}'])
def test_invalid_json_envelope_is_safe(body):
    client = GeminiEmbedder('secret', transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    with pytest.raises(EmbeddingError) as error: client.document('private-memory')
    assert error.value.reason == 'invalid_response'
    assert 'private' not in str(error.value) and 'secret' not in str(error.value)


@pytest.mark.parametrize('status,reason', [(400,'configuration_rejected'), (401,'configuration_rejected'),
    (403,'configuration_rejected'), (404,'configuration_rejected'), (429,'rate_limited'),
    (503,'provider_unavailable'), (302,'provider_unavailable')])
def test_provider_failure_is_fixed_no_retry_or_redirect(status, reason):
    handler = Mock(return_value=httpx.Response(status, text='private-provider-body',
        headers={'Location': 'https://untrusted.invalid/'}))
    client = GeminiEmbedder('secret', transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError) as error: client.query('private-question')
    assert error.value.reason == reason and error.value.status_code == status
    assert handler.call_count == 1
    assert 'private' not in str(error.value) and 'secret' not in str(error.value)


@pytest.mark.parametrize('failure,reason', [(httpx.ConnectTimeout,'connect_timeout'),
    (httpx.ReadTimeout,'read_timeout'), (httpx.WriteTimeout,'timeout'),
    (httpx.PoolTimeout,'timeout'), (httpx.ConnectError,'network_error'),
    (httpx.RemoteProtocolError,'network_error')])
def test_network_failure_does_not_retry_or_expose_message(failure, reason):
    def handler(request): raise failure('secret-key private-memory')
    mock = Mock(side_effect=handler)
    with pytest.raises(EmbeddingError) as error:
        GeminiEmbedder('secret-key', transport=httpx.MockTransport(mock)).query('private-memory')
    assert mock.call_count == 1 and error.value.reason == reason
    assert 'secret-key' not in str(error.value) and 'private-memory' not in str(error.value)


def test_response_size_is_bounded():
    client = GeminiEmbedder('key', transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b' '*(MAX_RESPONSE_BYTES+1))))
    with pytest.raises(EmbeddingError, match='response_too_large'): client.query('query')


@pytest.mark.parametrize('timeout', [0, 121, True, '60', float('inf'), float('nan')])
def test_invalid_timeout_is_rejected(timeout):
    with pytest.raises(ValueError): GeminiEmbedder('key', read_timeout=timeout)


def test_cli_preview_never_initializes_client_or_loads_settings(monkeypatch, capsys):
    factory = Mock(side_effect=AssertionError('no provider'))
    monkeypatch.setattr(try_embeddings, 'GeminiEmbedder', factory)
    monkeypatch.setattr(try_embeddings, 'get_settings', Mock(side_effect=AssertionError('no secrets')))
    assert try_embeddings.main(['--kind','query']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['provider_calls'] == 0 and result['planned_calls'] == 1
    assert result['synthetic_only'] and not result['database_written']
    factory.assert_not_called()


def test_cli_explicit_run_sends_only_fixed_synthetic_text_and_no_vector_output(monkeypatch, capsys):
    from app.services.embeddings import EmbeddingResult
    client = Mock()
    client.document.return_value = EmbeddingResult(tuple([1]+[0]*(DIMENSIONS-1)), 'hash', 'RETRIEVAL_DOCUMENT')
    factory = Mock(return_value=client)
    monkeypatch.setattr(try_embeddings, 'GeminiEmbedder', factory)
    monkeypatch.setattr(try_embeddings, 'get_settings', lambda: Settings(gemini_api_key='secret'))
    assert try_embeddings.main(['--run']) == 0
    client.document.assert_called_once_with(try_embeddings.SAMPLES['document'])
    client.query.assert_not_called()
    output = capsys.readouterr().out; result = json.loads(output)
    assert result['state'] == 'succeeded' and result['vector_dimensions'] == 768
    assert 'vector' not in result and 'secret' not in output


def test_cli_failure_is_reported_once_and_no_implicit_retry(monkeypatch, capsys):
    client = Mock()
    client.query.side_effect = EmbeddingError('rate_limited',status_code=429)
    monkeypatch.setattr(try_embeddings, 'GeminiEmbedder', lambda key: client)
    monkeypatch.setattr(try_embeddings, 'get_settings', lambda: Settings(gemini_api_key='secret'))
    assert try_embeddings.main(['--kind','query','--run']) == 1
    client.query.assert_called_once()
    result = json.loads(capsys.readouterr().out)
    assert result['reason'] == 'rate_limited' and result['http_status'] == 429


def test_cli_missing_key_does_not_initialize_provider(monkeypatch, capsys):
    factory = Mock()
    monkeypatch.setattr(try_embeddings, 'GeminiEmbedder', factory)
    monkeypatch.setattr(try_embeddings, 'get_settings', lambda: Settings())
    assert try_embeddings.main(['--run']) == 1
    assert json.loads(capsys.readouterr().out)['provider_calls'] == 0
    factory.assert_not_called()
