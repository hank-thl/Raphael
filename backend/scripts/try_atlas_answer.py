"""Read-only Atlas retrieval → existing answer trial. No production routing or prompt changes."""
import argparse
import json
from datetime import datetime, timezone
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.analyses import AnalysesRepository
from app.repositories.memories import MemoriesRepository
from app.repositories.memory_embeddings import MemoryEmbeddingsRepository
from app.services.embeddings import EmbeddingError, GeminiEmbedder, PROFILE
from app.services.gemini import GeminiError, GeminiExtractor
from app.services.memory_answer import ANSWER_PROMPT_VERSION, ANSWER_VERSION, AnswerError
from app.services.memory_capture import MemoryCaptureService
from app.services.memory_embeddings import MemoryEmbeddingError, MemoryEmbeddingService
from app.services.memory_search import MemorySearchError, MemorySearchService
from app.services.memory_versions import MemoryVersionsService
from app.services.chat_answer import build_chat_answerer
from scripts.evaluate_memory_retrieval import PRIVATE_DIR, checkpoint
from scripts.evaluate_retrieval_answers import MeasuredExtractor, SelectedAnswerService, TimedRecords, record_time
from scripts.try_vector_search import check_index

CASES = {'travel': '我的長崎旅行目前怎麼規劃？', 'exercise': '我健身時身體有什麼不舒服？',
         'expense-missing': '我上次洗頭花多少錢？'}


class TimedQuery:
    def __init__(self, provider, metrics):
        self.provider, self.metrics = provider, metrics

    def query(self, text):
        started = perf_counter()
        try:
            return self.provider.query(text)
        finally:
            record_time(self.metrics, 'query_provider', started)


class TimedCollection:
    def __init__(self, collection, metrics):
        self.collection, self.metrics = collection, metrics

    def aggregate(self, *args, **kwargs):
        started = perf_counter()
        try:
            return list(self.collection.aggregate(*args, **kwargs))
        finally:
            record_time(self.metrics, 'atlas_aggregate', started)


class TimedBatchValidation:
    def __init__(self, embeddings, metrics):
        self.embeddings, self.metrics = embeddings, metrics

    def current_many(self, uid, identifiers):
        started = perf_counter()
        try:
            return self.embeddings.current_many(uid, identifiers)
        finally:
            record_time(self.metrics, 'candidate_batch_validation', started)

    def recheck_many(self, uid, expected):
        started = perf_counter()
        try:
            return self.embeddings.recheck_many(uid, expected)
        finally:
            record_time(self.metrics, 'selected_batch_recheck', started)


def run_trial(reader, collection, uid, embedder, extractor, question):
    metrics, usage = {}, {}
    wall_start = perf_counter()
    indexed = perf_counter()
    check_index(collection)
    record_time(metrics, 'index_check', indexed)
    embeddings = MemoryEmbeddingService(reader, MemoryEmbeddingsRepository(collection), embedder)
    started = perf_counter()
    search = MemorySearchService(TimedCollection(collection, metrics),
        TimedBatchValidation(embeddings, metrics), TimedQuery(embedder, metrics))
    retrieved = search.search(uid, question)
    metrics['retrieval_total_ms'] = (perf_counter() - started) * 1000
    metrics['retrieval_validation_and_other_ms'] = max(0, metrics['retrieval_total_ms']
        - metrics.get('query_provider_ms', 0) - metrics.get('atlas_aggregate_ms', 0))
    expected = [{'memory_id': row['memory_id'], 'fingerprint': row['fingerprint']} for row in retrieved['results']]
    # Binding the retrieval fingerprint prevents newer/rebuilt content from silently
    # replacing the content ranked by Atlas across the search→answer boundary.
    answer_reader = SimpleNamespace(memories=reader.memories, get_eligible_many=reader.get_eligible_many,
        capture=SimpleNamespace(records=TimedRecords(reader.capture.records, metrics)))
    service = SelectedAnswerService(answer_reader, MeasuredExtractor(extractor, usage, metrics=metrics),
                                    uid, expected, metrics=metrics)
    started = perf_counter()
    generated = service.generate(uid, question)
    metrics['answer_total_ms'] = (perf_counter() - started) * 1000
    metrics['answer_validation_and_other_ms'] = max(0, metrics['answer_total_ms']
        - metrics.get('answer_provider_ms', 0) - metrics.get('selected_collect_ms', 0)
        - metrics.get('answer_record_read_ms', 0))
    metrics['wall_ms'] = (perf_counter() - wall_start) * 1000
    return {'version': 'atlas-answer-trial-v2-batch', 'question': question, 'profile': PROFILE,
        'answer_prompt': ANSWER_PROMPT_VERSION, 'answer_version': ANSWER_VERSION,
        'retrieval': retrieved, 'answer': generated['answer'].model_dump(), 'inputs': generated['inputs'],
        'timings': metrics, 'answer_usage': usage, 'database_written': False,
        'conversations_written': False, 'human_review_required': True, 'semantic_passed': None,
        'timing_note': 'retrieval/answer totals contain their provider and validation subitems; wall excludes report write.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=tuple(CASES), default='travel')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--chat-routing', action='store_true', help='Use the production chat factory with no previous context; never save dialogue.')
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({'mode': 'preview', 'case': args.case, 'chat_routing': args.chat_routing, 'planned_provider_calls_max': 2,
            'provider_calls': 0, 'database_accessed': False, 'database_written': False}))
        return 0
    connection = None
    try:
        settings = get_settings()
        if not all((settings.admin_uid, settings.mongodb_uri, settings.gemini_api_key, settings.gemini_model)):
            raise MemorySearchError('not_configured')
        connection = DatabaseConnection(settings)
        connection.open()
        db = connection.database
        if db is None:
            raise MemorySearchError('database_unavailable')
        reader = MemoryVersionsService(MemoryCaptureService(db['records'], AnalysesRepository(db['analyses']), None, ''),
                                       MemoriesRepository(db['memories']))
        if args.chat_routing:
            started = perf_counter()
            generated = build_chat_answerer(reader, db, settings).generate(settings.admin_uid, CASES[args.case])
            result = {'version': 'chat-routing-trial-v1', 'question': CASES[args.case],
                'answer': generated['answer'].model_dump(), 'inputs': generated['inputs'],
                'retrieval': generated.get('retrieval', {'mode': 'direct', 'reason': 'pilot_disabled'}),
                'answer_prompt': ANSWER_PROMPT_VERSION, 'answer_version': ANSWER_VERSION,
                'timings': {'wall_ms': (perf_counter() - started) * 1000},
                'database_written': False, 'conversations_written': False,
                'human_review_required': True, 'semantic_passed': None}
        else:
            result = run_trial(reader, db['memory_embeddings'], settings.admin_uid,
                GeminiEmbedder(settings.gemini_api_key), GeminiExtractor(settings.gemini_api_key, settings.gemini_model), CASES[args.case])
        result.update(case=args.case, model=settings.gemini_model, state='succeeded',
                      created_at=datetime.now(timezone.utc).isoformat())
        path = PRIVATE_DIR / ('atlas-answer-evaluation-' + uuid4().hex + '.json')
        checkpoint(path, result)
        print(json.dumps({'state': 'succeeded', 'report': str(path), 'database_written': False,
                          'elapsed_ms': round(result['timings']['wall_ms'])}))
        return 0
    except Exception as exc:
        reason = exc.reason if isinstance(exc, (EmbeddingError, GeminiError)) else str(exc) if isinstance(
            exc, (MemorySearchError, MemoryEmbeddingError, AnswerError)) else 'trial_or_report_failed'
        print(json.dumps({'state': 'failed', 'reason': reason, 'database_written': False,
                          'automatic_retry': False, 'provider_calls_may_have_occurred': True}))
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
