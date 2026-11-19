"""Small, read-only Atlas ENN trial. Vector hits are candidates, never authorization."""
import hashlib
import math

from app.services.embeddings import PROFILE, normalize
from app.services.memory_embeddings import MemoryEmbeddingError

INDEX_NAME = 'memory_vector_v1'
MAX_HITS = 30


class MemorySearchError(ValueError):
    pass


def search_pipeline(uid, vector):
    if not isinstance(uid, str) or not uid.strip():
        raise MemorySearchError('owner_required')
    normalized = list(normalize(list(vector)))
    return [{'$vectorSearch': {'index': INDEX_NAME, 'path': 'vector',
        'queryVector': normalized, 'exact': True, 'limit': MAX_HITS,
        'filter': {'user_uid': {'$eq': uid}, 'profile': {'$eq': PROFILE},
                   'task': {'$eq': 'RETRIEVAL_DOCUMENT'}}}},
        {'$project': {'_id': 1, 'user_uid': 1, 'memory_id': 1, 'revision': 1,
            'fingerprint': 1, 'profile': 1, 'task': 1, 'text_hash': 1, 'model': 1,
            'dimensions': 1, 'score': {'$meta': 'vectorSearchScore'}}}]


class MemorySearchService:
    def __init__(self, collection, embeddings, embedder):
        self.collection, self.embeddings, self.embedder = collection, embeddings, embedder

    def search(self, uid, question, *, limit=5):
        if not isinstance(uid, str) or not uid.strip():
            raise MemorySearchError('owner_required')
        if type(limit) is not int or not 1 <= limit <= 10:
            raise MemorySearchError('invalid_limit')
        # Same transport applies the existing byte budget; no truncation or retries.
        result = self.embedder.query(question)
        if (result.task != 'RETRIEVAL_QUERY' or result.profile != PROFILE
                or result.text_hash != hashlib.sha256(question.encode()).hexdigest()):
            raise MemorySearchError('embedding_mismatch')
        hits = list(self.collection.aggregate(search_pipeline(uid, result.vector), maxTimeMS=10000))
        if len(hits) > MAX_HITS:
            raise MemorySearchError('invalid_search_result')
        selected = []
        seen = set()
        rejected = 0
        candidate_ids = list(dict.fromkeys(hit['memory_id'] for hit in hits
            if hit.get('user_uid') == uid and hit.get('profile') == PROFILE
            and hit.get('task') == 'RETRIEVAL_DOCUMENT' and isinstance(hit.get('memory_id'), str)))
        current_by_id = self.embeddings.current_many(uid, candidate_ids) if candidate_ids else {}
        fields = ('_id', 'user_uid', 'memory_id', 'revision', 'fingerprint', 'profile',
                  'task', 'text_hash', 'model', 'dimensions')
        covered = {hit['memory_id']: hit['fingerprint'] for hit in hits
            if hit.get('memory_id') in current_by_id
            and all(hit.get(k) == current_by_id[hit['memory_id']].get(k) for k in fields)}
        for hit in hits:
            # Do not trust even a returned Atlas hit to satisfy its pre-filter.
            if (hit.get('user_uid') != uid or hit.get('profile') != PROFILE
                    or hit.get('task') != 'RETRIEVAL_DOCUMENT'):
                rejected += 1
                continue
            mid = hit.get('memory_id')
            if not isinstance(mid, str) or mid in seen:
                rejected += 1
                continue
            current = current_by_id.get(mid)
            if current is None or any(hit.get(k) != current.get(k) for k in fields):
                rejected += 1
                continue
            score = hit.get('score')
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise MemorySearchError('invalid_search_result')
            selected.append((hit, current))
            seen.add(mid)
            if len(selected) == limit:
                break
        # Recheck the whole returned set after processing the other hits.
        if selected:
            self.embeddings.recheck_many(uid, {hit['memory_id']: {k: v for k, v in current.items()
                if k not in {'vector', 'created_at'}} for hit, current in selected})
        output = []
        for hit, current in selected:
            output.append({'memory_id': hit['memory_id'], 'revision': hit['revision'],
                           'fingerprint': hit['fingerprint'], 'score': hit['score']})
        return {'results': output, 'candidate_count': len(hits), 'rejected_count': rejected,
                'covered_inputs': [{'memory_id': mid, 'fingerprint': fp} for mid, fp in covered.items()],
                'validation_version': 'batch-sources-v1',
                'candidate_cap_reached': len(hits) == MAX_HITS,
                'relevance_verified': False}
