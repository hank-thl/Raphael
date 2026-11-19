"""Standalone text embedding transport. No database, automatic retries or worker hooks."""
import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Literal

import httpx

MODEL = 'gemini-embedding-001'
DIMENSIONS = 768
PROFILE = 'gemini-embedding-001-768-text-v1-l2'
# A conservative per-call byte budget, NOT a tokenizer or a character/token equation.
# Longer production memories require a separate token-budget policy; never slice text.
MAX_INPUT_BYTES = 1800
MAX_RESPONSE_BYTES = 128 * 1024
Task = Literal['RETRIEVAL_DOCUMENT', 'RETRIEVAL_QUERY']


class EmbeddingError(Exception):
    def __init__(self, reason: str, *, status_code: int | None = None):
        self.reason, self.status_code = reason, status_code
        super().__init__(f'Embedding failed: {reason}')


@dataclass(frozen=True)
class EmbeddingResult:
    vector: tuple[float, ...] = field(repr=False)
    text_hash: str
    task: Task
    profile: str = PROFILE


def normalize(values):
    if not isinstance(values, list) or len(values) != DIMENSIONS:
        raise EmbeddingError('invalid_vector')
    try:
        if any(type(v) not in (int, float) for v in values):
            raise ValueError()
        numbers = [float(v) for v in values]
        if not all(math.isfinite(v) for v in numbers):
            raise ValueError()
        scale = max(abs(v) for v in numbers)
        if scale == 0:
            raise ValueError()
        # Scaling avoids overflow/underflow for otherwise finite valid components.
        scaled = [v / scale for v in numbers]
        norm = math.sqrt(math.fsum(v * v for v in scaled))
        return tuple(v / norm for v in scaled)
    except (ValueError, TypeError, OverflowError):
        raise EmbeddingError('invalid_vector') from None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError()


class GeminiEmbedder:
    def __init__(self, api_key: str, *, read_timeout: float = 60,
                 transport: httpx.BaseTransport | None = None):
        if type(read_timeout) not in (int, float) or not math.isfinite(read_timeout) or not 1 <= read_timeout <= 120:
            raise ValueError('read_timeout must be between 1 and 120 seconds')
        self._api_key, self._read_timeout, self._transport = api_key, read_timeout, transport

    def __repr__(self):
        return f'GeminiEmbedder(profile={PROFILE!r})'

    def document(self, text: str) -> EmbeddingResult:
        return self._embed(text, 'RETRIEVAL_DOCUMENT')

    def query(self, text: str) -> EmbeddingResult:
        return self._embed(text, 'RETRIEVAL_QUERY')

    def _embed(self, text: str, task: Task) -> EmbeddingResult:
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingError('invalid_input')
        try:
            encoded = text.encode('utf-8')
        except UnicodeError:
            raise EmbeddingError('invalid_input') from None
        if len(encoded) > MAX_INPUT_BYTES:
            raise EmbeddingError('input_limit')
        if not isinstance(self._api_key, str) or not self._api_key.strip():
            raise EmbeddingError('not_configured')
        payload = {'model': f'models/{MODEL}', 'content': {'parts': [{'text': text}]},
                   'taskType': task, 'outputDimensionality': DIMENSIONS}
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:embedContent'
        timeout = httpx.Timeout(connect=10, read=self._read_timeout, write=10, pool=5)
        try:
            with httpx.Client(timeout=timeout, transport=self._transport, trust_env=False,
                              follow_redirects=False) as client:
                with client.stream('POST', url, headers={'x-goog-api-key': self._api_key}, json=payload) as response:
                    if not 200 <= response.status_code < 300:
                        status = response.status_code
                        reason = ('rate_limited' if status == 429 else 'configuration_rejected'
                                  if status in {400, 401, 403, 404} else 'provider_unavailable')
                        raise EmbeddingError(reason, status_code=status)
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise EmbeddingError('response_too_large')
                        raw.extend(chunk)
        except httpx.TimeoutException as exc:
            reason = ('connect_timeout' if isinstance(exc, httpx.ConnectTimeout) else
                      'read_timeout' if isinstance(exc, httpx.ReadTimeout) else 'timeout')
            raise EmbeddingError(reason) from None
        except httpx.HTTPError:
            raise EmbeddingError('network_error') from None
        try:
            body = json.loads(raw, object_pairs_hook=_unique, parse_constant=_reject_constant)
            values = body['embedding']['values']
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise EmbeddingError('invalid_response') from None
        return EmbeddingResult(normalize(values), hashlib.sha256(encoded).hexdigest(), task)
