"""Opt-in document vector persistence. No worker, public API or search integration."""
import hashlib
import math
from datetime import datetime, timezone

from app.services.embeddings import DIMENSIONS, MAX_INPUT_BYTES, MODEL, PROFILE, EmbeddingError, normalize
from app.services.memory_answer import MemoryAnswerService
from app.services.memory_capture import digest


class MemoryEmbeddingError(ValueError):
    pass


class MemoryEmbeddingService:
    def __init__(self, reader, repository, embedder):
        self.reader, self.repository, self.embedder = reader, repository, embedder

    def snapshot(self, uid, memory_id):
        if not uid:
            raise MemoryEmbeddingError('memory_unavailable')
        row = self.reader.memories.get(uid, memory_id)
        rows = self.reader.get_eligible_many(uid, [row] if row else [])
        if (len(rows) != 1 or rows[0].get('_id') != memory_id or rows[0].get('user_uid') != uid
                or rows[0].get('lifecycle') != 'candidate'):
            raise MemoryEmbeddingError('memory_unavailable')
        return self.metadata(rows[0])

    @staticmethod
    def metadata(row):
        uid, memory_id = row['user_uid'], row['_id']
        text = row['versions'][-1]['content']['text']
        if not isinstance(text, str) or not text.strip():
            raise MemoryEmbeddingError('invalid_input')
        if len(text.encode('utf-8')) > MAX_INPUT_BYTES:
            raise EmbeddingError('input_limit')
        fingerprint = MemoryAnswerService.dependency(row)['fingerprint']
        metadata = {'user_uid': uid, 'memory_id': memory_id, 'revision': row['revision'],
            'fingerprint': fingerprint, 'text_hash': hashlib.sha256(text.encode()).hexdigest(),
            'profile': PROFILE, 'model': MODEL, 'dimensions': DIMENSIONS, 'task': 'RETRIEVAL_DOCUMENT'}
        metadata['_id'] = digest(['memory-embedding-v1', uid, memory_id, row['revision'], fingerprint, PROFILE])
        return metadata, text

    def snapshots_many(self, uid, memory_ids):
        if not uid or len(memory_ids) > 30 or len(set(memory_ids)) != len(memory_ids):
            raise MemoryEmbeddingError('invalid_batch')
        if not memory_ids:
            return {}
        allowed = set(memory_ids)
        rows = list(self.reader.memories.collection.find(
            {'user_uid': uid, 'lifecycle': 'candidate', '_id': {'$in': memory_ids}}))
        rows = [row for row in rows if row.get('user_uid') == uid
                and row.get('lifecycle') == 'candidate' and row.get('_id') in allowed]
        valid = self.reader.get_eligible_many(uid, rows)
        result = {}
        for row in valid:
            if row.get('user_uid') != uid or row.get('lifecycle') != 'candidate' or row.get('_id') not in allowed:
                raise MemoryEmbeddingError('invalid_batch')
            if row['_id'] in result:
                raise MemoryEmbeddingError('invalid_batch')
            result[row['_id']] = self.metadata(row)[0]
        return result

    def recheck_many(self, uid, expected):
        if self.snapshots_many(uid, list(expected)) != expected:
            raise MemoryEmbeddingError('sources_changed')

    def current_many(self, uid, memory_ids):
        """Two fresh batched source reads around one batched cache read; no cross-request cache."""
        expected = self.snapshots_many(uid, memory_ids)
        if not expected:
            return {}
        documents = self.repository.get_many(uid, [row['_id'] for row in expected.values()])
        by_id = {row['_id']: row for row in documents if row.get('user_uid') == uid}
        result = {}
        for mid, metadata in expected.items():
            cached = by_id.get(metadata['_id'])
            if cached is None:
                continue
            try:
                self.validate(cached, metadata)
            except MemoryEmbeddingError:
                continue  # Same exclusion as a corrupt single-hit cache during search.
            result[mid] = cached
        # A change during cache loading aborts the request, never returns stale evidence.
        self.recheck_many(uid, expected)
        return result

    def recheck(self, uid, memory_id, expected):
        try:
            current, _ = self.snapshot(uid, memory_id)
        except (MemoryEmbeddingError, EmbeddingError):
            raise MemoryEmbeddingError('sources_changed') from None
        if current != expected:
            raise MemoryEmbeddingError('sources_changed')

    @staticmethod
    def validate(document, expected):
        if not document or any(document.get(k) != v for k, v in expected.items()):
            raise MemoryEmbeddingError('invalid_cache')
        vector = document.get('vector')
        normalize(vector)  # Strict dimension, numeric, finite and nonzero validation.
        if not math.isclose(math.hypot(*vector), 1.0, rel_tol=1e-6, abs_tol=1e-6):
            raise MemoryEmbeddingError('invalid_cache')
        return document

    def current(self, uid, memory_id):
        """Never expose a vector without fresh owner, version and source checks."""
        expected, _ = self.snapshot(uid, memory_id)
        cached = self.repository.get(uid, expected['_id'])
        if cached is None:
            return None
        self.validate(cached, expected)
        self.recheck(uid, memory_id, expected)
        return cached

    def save(self, uid, memory_id, *, expected_snapshot=None, checkpoint=lambda: None):
        checkpoint()
        expected, text = self.snapshot(uid, memory_id)
        if expected_snapshot is not None and expected != expected_snapshot:
            raise MemoryEmbeddingError('sources_changed')
        cached = self.repository.get(uid, expected['_id'])
        if cached is not None:
            self.validate(cached, expected)
            self.recheck(uid, memory_id, expected)
            return cached, True
        checkpoint()
        result = self.embedder.document(text)
        if result.profile != PROFILE or result.task != 'RETRIEVAL_DOCUMENT' or result.text_hash != expected['text_hash']:
            raise MemoryEmbeddingError('embedding_mismatch')
        vector = list(normalize(list(result.vector)))
        self.recheck(uid, memory_id, expected)
        checkpoint()
        document = {**expected, 'vector': vector, 'created_at': datetime.now(timezone.utc)}
        saved = self.repository.create_once(document)
        self.validate(saved, expected)
        # A source can change across collections while insert runs. Such a row
        # remains physically cached, but current() will never return it.
        self.recheck(uid, memory_id, expected)
        return saved, False
