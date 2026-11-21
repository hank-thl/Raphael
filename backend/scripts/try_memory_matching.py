"""Synthetic new/uncertain/supplement evaluation, isolated by scenario owner."""
import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.analyses import AnalysesRepository
from app.repositories.content_types import ContentTypesRepository
from app.repositories.memories import MemoriesRepository
from app.services.classification import ClassificationService
from app.services.gemini import CONTRACT_VERSION, PROMPT_VERSION, GeminiError, GeminiExtractor
from app.services.memory_capture import CaptureError, MemoryCaptureService
from app.services.memory_matching import GeminiMemoryMatcher, MATCH_VERSION
from app.services.memory_routing import MemoryRoutingService
from app.services.memory_versions import MemoryVersionsService
from scripts.try_memory_capture import describe_memory

SCENARIOS = {
    'new': {'baselines': ['今天買了一杯拿鐵，花了120元。'],
            'incoming': '今天去上陶藝課，覺得很開心。', 'expected': 'new'},
    'uncertain': {'baselines': ['今天上午在甲咖啡店買了一杯拿鐵。', '今天下午在乙咖啡店也買了一杯拿鐵。'],
                  'incoming': '買拿鐵花了120元。', 'expected': 'uncertain'},
    'supplement': {'baselines': ['今天買了茶飲和甜點，共180元。'],
                   'incoming': '茶飲70元，甜點110元。', 'expected': 'supplement'},
    'continuation': {'baselines': ['吃口服a酸結果大爆痘'],
                     'incoming': '醫生還有開外用a酸', 'expected': 'supplement'},
    'explicit_reference': {'baselines': ['上次修腳踏車後，騎起來還是有異音。',
        '我另外買了一個新鈴鐺。'],
        'incoming': '補充修腳踏車後還有異音那件事，師傅也幫我換了鈴鐺。',
        'expected': 'supplement'},
    'different_episode': {'baselines': ['去年在甲診所看診，醫生開了外用a酸。'],
        'incoming': '今天到乙診所看診，另一位醫生也開了外用a酸。', 'expected': 'new'},
    'ambiguous_continuation': {'baselines': ['這次在甲診所治療皮膚，醫生開了口服藥。',
        '上次在乙診所看診，醫生也開了口服藥。'], 'incoming': '醫生還有開外用藥。', 'expected': 'uncertain'},
    'topic_only': {'baselines': ['今天騎車回家覺得很累。'],
        'incoming': '另外，今天學會一道新菜。', 'expected': 'new'},
}
PREFIX = 'memory_matching_eval'


def seed(records, case):
    scenario = SCENARIOS[case]
    uid = f'__raphael_matching_eval_{CONTRACT_VERSION}_{MATCH_VERSION}_{PROMPT_VERSION}_{case}__'
    ids = []
    for index, content in enumerate([*scenario['baselines'], scenario['incoming']]):
        identifier = ObjectId(hashlib.sha256(f'{uid}:{index}'.encode()).hexdigest()[:24])
        records.update_one({'_id': identifier, 'user_uid': uid}, {'$setOnInsert': {
            'content': content, 'created_at': datetime(2026, 11, 14, 4, tzinfo=timezone.utc) + timedelta(minutes=index),
            'deleted_at': None}}, upsert=True)
        ids.append(str(identifier))
    return uid, ids


def evaluate(db, model, extractor, case, embedder=None):
    records = db[f'{PREFIX}_records']
    uid, ids = seed(records, case)
    capture = MemoryCaptureService(records, AnalysesRepository(db[f'{PREFIX}_analyses']),
        ClassificationService(extractor, ContentTypesRepository(db[f'{PREFIX}_content_types'])), model)
    versions = MemoryVersionsService(capture, MemoriesRepository(db[f'{PREFIX}_memories']))
    from app.services.topics import MemoryTopics
    capture.classifier.topics = MemoryTopics(versions)
    baseline_ids = []
    # Fixture baselines deliberately represent distinct events. Only the incoming
    # candidate is routed by Gemini; do not secretly match baselines to each other.
    for identifier in ids[:-1]:
        analysis, _ = capture.capture(uid, identifier)
        if len(analysis['memories']) != 1:
            raise CaptureError('single_baseline_candidate_required')
        for candidate in analysis['memories']:
            memory = versions.create(uid, analysis['_id'], candidate['_id'])
            baseline_ids.append(memory['_id'])
    incoming, analysis_reused = capture.capture(uid, ids[-1])
    if len(incoming['memories']) != 1:
        raise CaptureError('single_incoming_candidate_required')
    router = MemoryRoutingService(capture, versions, GeminiMemoryMatcher(extractor),
                                 AnalysesRepository(db[f'{PREFIX}_routes']))
    outcomes = [router.route(uid, incoming['_id'], item['_id']) for item in incoming['memories']]
    embedding_results = []
    if embedder is not None:
        from app.repositories.embedding_jobs import EmbeddingJobsRepository
        from app.repositories.memory_embeddings import MemoryEmbeddingsRepository
        from app.services.memory_embeddings import MemoryEmbeddingService
        from app.services.embedding_worker import process_embedding_job
        jobs = EmbeddingJobsRepository(db[f'{PREFIX}_memories'])
        service = MemoryEmbeddingService(versions,
            MemoryEmbeddingsRepository(db[f'{PREFIX}_embeddings']), embedder)
        for identifier in dict.fromkeys(row['memory_id'] for row in outcomes):
            claimed = jobs.claim(uid, memory_id=identifier)
            if claimed is not None:
                process_embedding_job(jobs, service, claimed)
            saved = db[f'{PREFIX}_memories'].find_one({'_id': identifier, 'user_uid': uid})
            cached = service.current(uid, identifier)
            embedding_results.append({'memory_id': identifier, 'revision': saved['revision'],
                'job_state': saved.get('embedding_processing', {}).get('state'),
                'job_revision': saved.get('embedding_processing', {}).get('revision'),
                'error': saved.get('embedding_processing', {}).get('error'),
                'current_vector_available': cached is not None,
                'vector_revision': cached['revision'] if cached else None})
    return {'case': case, 'expected_action': SCENARIOS[case]['expected'],
            'expectation_met': bool(outcomes) and all(
                row['action'] == SCENARIOS[case]['expected'] and
                (case != 'explicit_reference' or row['memory_id'] == baseline_ids[0]) for row in outcomes),
            'analysis_reused': analysis_reused, 'matching_version': MATCH_VERSION,
            'planned_provider_calls_max': len(SCENARIOS[case]['baselines']) + 2 + int(embedder is not None),
            'embedding_results': embedding_results,
            'extraction_prompt_version': PROMPT_VERSION, 'routing': outcomes,
            'baseline_memories': [describe_memory(versions, uid, identifier) for identifier in baseline_ids],
            'result_memories': [describe_memory(versions, uid, row['memory_id']) for row in outcomes]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=list(SCENARIOS), required=True)
    parser.add_argument('--save', action='store_true', help='Write synthetic evaluation collections; call Gemini on cache misses')
    parser.add_argument('--with-embedding', action='store_true', help='Process result jobs in synthetic collections; at most one extra embedding call')
    args = parser.parse_args()
    if not args.save:
        print(json.dumps(SCENARIOS[args.case], ensure_ascii=False, indent=2))
        print('Dry run: no database or API access. Add --save to execute.')
        return 0
    settings = get_settings()
    if not settings.gemini_api_key or not settings.gemini_model:
        print('Evaluation failed: not_configured')
        return 1
    connection = DatabaseConnection(settings)
    stage = 'database_initialization'
    try:
        connection.open()
        if connection.database is None:
            raise CaptureError('database_unavailable')
        stage = 'evaluate_synthetic_scenario'
        from app.services.embeddings import GeminiEmbedder
        result = evaluate(connection.database, settings.gemini_model,
                          GeminiExtractor(settings.gemini_api_key, settings.gemini_model), args.case,
                          GeminiEmbedder(settings.gemini_api_key) if args.with_embedding else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print('Saved synthetic data only in memory_matching_eval_* collections. No private records were read.')
        if not result['expectation_met']:
            print('Model decision differs from this scenario expectation; inspect the saved result. No forced answer or automatic retry.')
        return 0
    except (GeminiError, CaptureError) as exc:
        print(f'Evaluation failed: {exc.reason if isinstance(exc, GeminiError) else str(exc)}; stage={stage}')
        return 1
    except Exception:
        print(f'Evaluation failed: storage_or_validation_failed; stage={stage}. No raw errors displayed.')
        return 1
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
