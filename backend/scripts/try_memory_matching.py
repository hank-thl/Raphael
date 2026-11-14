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
from app.services.gemini import CONTRACT_VERSION, GeminiError, GeminiExtractor
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
}
PREFIX = 'memory_matching_eval'


def seed(records, case):
    scenario = SCENARIOS[case]
    uid = f'__raphael_matching_eval_{CONTRACT_VERSION}_{case}__'
    ids = []
    for index, content in enumerate([*scenario['baselines'], scenario['incoming']]):
        identifier = ObjectId(hashlib.sha256(f'{uid}:{index}'.encode()).hexdigest()[:24])
        records.update_one({'_id': identifier, 'user_uid': uid}, {'$setOnInsert': {
            'content': content, 'created_at': datetime(2026, 11, 14, 4, tzinfo=timezone.utc) + timedelta(minutes=index),
            'deleted_at': None}}, upsert=True)
        ids.append(str(identifier))
    return uid, ids


def evaluate(db, model, extractor, case):
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
        if not analysis['memories']:
            raise CaptureError('baseline_produced_no_candidates')
        for candidate in analysis['memories']:
            memory = versions.create(uid, analysis['_id'], candidate['_id'])
            baseline_ids.append(memory['_id'])
    incoming, analysis_reused = capture.capture(uid, ids[-1])
    router = MemoryRoutingService(capture, versions, GeminiMemoryMatcher(extractor),
                                 AnalysesRepository(db[f'{PREFIX}_routes']))
    outcomes = [router.route(uid, incoming['_id'], item['_id']) for item in incoming['memories']]
    return {'case': case, 'expected_action': SCENARIOS[case]['expected'],
            'expectation_met': bool(outcomes) and all(row['action'] == SCENARIOS[case]['expected'] for row in outcomes),
            'analysis_reused': analysis_reused, 'matching_version': MATCH_VERSION, 'routing': outcomes,
            'baseline_memories': [describe_memory(versions, uid, identifier) for identifier in baseline_ids],
            'result_memories': [describe_memory(versions, uid, row['memory_id']) for row in outcomes]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=list(SCENARIOS), required=True)
    parser.add_argument('--save', action='store_true', help='Write synthetic evaluation collections; call Gemini on cache misses')
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
        result = evaluate(connection.database, settings.gemini_model,
                          GeminiExtractor(settings.gemini_api_key, settings.gemini_model), args.case)
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
