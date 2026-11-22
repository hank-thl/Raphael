"""Feature-gated, explicitly allowlisted retrieval pilot; other users retain direct answers."""
from pymongo.errors import PyMongoError

from app.services.embeddings import EmbeddingError
from app.services.memory_answer import AnswerError, MemoryAnswerService
from app.services.memory_embeddings import MemoryEmbeddingError
from app.services.memory_search import MemorySearchError
from app.services.retrieved_answer import RetrievedMemoryAnswerService
from app.services.retrieval_context import retrieval_context
from app.services.vector_index import check_index


def build_chat_answerer(reader, db, settings):
    from app.repositories.memory_embeddings import MemoryEmbeddingsRepository
    from app.services.embeddings import GeminiEmbedder
    from app.services.gemini import GeminiExtractor
    from app.services.memory_embeddings import MemoryEmbeddingService
    from app.services.memory_search import MemorySearchService

    extractor = GeminiExtractor(settings.gemini_api_key, settings.gemini_model)
    if not settings.atlas_chat_enabled or not settings.admin_uid:
        return MemoryAnswerService(reader, extractor)
    collection = db['memory_embeddings']
    embedder = GeminiEmbedder(settings.gemini_api_key)
    embeddings = MemoryEmbeddingService(reader, MemoryEmbeddingsRepository(collection), embedder)
    return ChatAnswerService(reader, extractor, embeddings,
        MemorySearchService(collection, embeddings, embedder), collection,
        owner=settings.admin_uid, enabled=True, fallback_on_missing=True,
        allowed_uids=settings.atlas_chat_allowed_uids)


class ChatAnswerService(MemoryAnswerService):
    def __init__(self, reader, extractor, embeddings, search, collection, *, owner,
                 enabled, fallback_on_missing, allowed_uids=frozenset()):
        super().__init__(reader, extractor)
        self.embeddings, self.search, self.collection = embeddings, search, collection
        self.owner, self.enabled = owner, enabled
        self.allowed_uids = frozenset(allowed_uids) | ({owner} if owner else set())
        self.fallback_on_missing = fallback_on_missing

    def direct(self, uid, question, previous, reason):
        result = super().generate(uid, question, previous)
        result['retrieval'] = {'mode': 'direct', 'reason': reason}
        return result

    def unavailable(self, uid, question, previous):
        if self.fallback_on_missing:
            return self.direct(uid, question, previous, 'coverage_unavailable')
        raise AnswerError('retrieval_incomplete')

    def generate(self, uid, question, previous=()):
        if not self.enabled or not self.owner or uid not in self.allowed_uids:
            return self.direct(uid, question, previous, 'pilot_disabled')
        if not isinstance(question, str) or not question.strip() or len(question) > 10000:
            raise AnswerError('invalid_question')
        try:
            before = self.collect(uid)
            before_fingerprint = self.fingerprint(before)
            context = retrieval_context(uid, question, previous, before)
            if not context['query_fits']:
                return self.direct(uid, question, previous, 'query_context_limit')
            ids = [row['_id'] for row in before]
            if len(ids) > 30:
                return self.unavailable(uid, question, previous)
            vectors = self.embeddings.current_many(uid, ids)
            dependencies = {row['_id']: self.dependency(row)['fingerprint'] for row in before}
            if set(vectors) != set(ids) or any(vectors[mid]['fingerprint'] != fp for mid, fp in dependencies.items()):
                return self.unavailable(uid, question, previous)
            # No on-demand document embeddings. This pilot never writes vectors.
            check_index(self.collection)
            retrieved = self.search.search(uid, context['query'])
            # Atlas indexing is asynchronous; even existing vectors may not appear
            # yet. Only use retrieval if every eligible memory is represented in
            # the bounded candidate set, before selecting its top five.
            covered = {row['memory_id']: row['fingerprint'] for row in retrieved['covered_inputs']}
            if retrieved['candidate_cap_reached'] or any(covered.get(mid) != fp for mid, fp in dependencies.items()):
                return self.unavailable(uid, question, previous)
            if self.fingerprint([row for row in self.collect(uid) if row['_id'] in dependencies]) != before_fingerprint:
                raise AnswerError('sources_changed')
            expected = [{'memory_id': row['memory_id'], 'fingerprint': row['fingerprint']}
                        for row in retrieved['results']]
            selected_ids = {item['memory_id'] for item in expected}
            expected.extend({'memory_id': mid, 'fingerprint': dependencies[mid]}
                for mid in context['cited_ids'] if mid not in selected_ids)
            result = RetrievedMemoryAnswerService(self.reader, self.extractor, uid, expected).generate(
                uid, question, context['previous'])
            if self.fingerprint([row for row in self.collect(uid) if row['_id'] in dependencies]) != before_fingerprint:
                raise AnswerError('sources_changed')
            # Old uncited inputs may have affected the wording of prior answers.
            # Keep them as invalidation dependencies without supplying every Memory.
            tracked = {item['memory_id']: item['fingerprint'] for item in result['inputs']}
            tracked.update(context['dependency_fingerprints'])
            result['inputs'] = [{'memory_id': mid, 'fingerprint': fp} for mid, fp in tracked.items()]
            result['retrieval'] = {'mode': 'atlas', 'version': 'chat-atlas-context-v2',
                'validation_version': retrieved['validation_version'], 'candidate_count': retrieved['candidate_count'],
                'context_turns': len(context['previous']), 'context_memory_count': len(context['cited_ids'])}
            return result
        except (EmbeddingError, MemorySearchError, MemoryEmbeddingError, PyMongoError):
            # Fail once using the existing conversation failure flow. Never retry
            # or silently switch to a second paid answer following a search error.
            raise AnswerError('retrieval_unavailable') from None
