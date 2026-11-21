"""Read-only, owner-scoped routing inspection; no model calls or database writes."""
import argparse
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.analyses import AnalysesRepository
from app.repositories.memories import MemoriesRepository
from app.services.memory_capture import MemoryCaptureService, CaptureError, digest, snapshots
from app.services.memory_versions import MemoryVersionsService
from app.services.memory_answer import MemoryAnswerService
from scripts.backfill_memory_embeddings import target_uid
from scripts.evaluate_memory_retrieval import PRIVATE_DIR, checkpoint


def collect(db, uid, text, limit=5):
    if not uid or not text.strip() or len(text) > 80 or not 1 <= limit <= 10:
        raise ValueError('invalid_inspection')
    records = db['records']
    rows = list(records.find({'user_uid': uid, 'deleted_at': None,
        'content': {'$regex': re.escape(text), '$options': 'i'}}).sort([('created_at', -1), ('_id', -1)]).limit(limit + 1))
    selected = rows[:limit]
    capture = MemoryCaptureService(records, AnalysesRepository(db['analyses']), None, '')
    versions = MemoryVersionsService(capture, MemoriesRepository(db['memories']))
    output, checks = [], []
    for row in selected:
        if row.get('user_uid') != uid or row.get('deleted_at') is not None:
            raise ValueError('sources_changed')
        rid = str(row['_id'])
        analyses = list(db['analyses'].find({'user_uid': uid, 'target_record_id': rid}).limit(21))
        plans = list(db['memory_routes'].find({'user_uid': uid, 'sources.0.record_id': rid}).limit(21))
        if len(analyses) > 20 or len(plans) > 20:
            raise ValueError('inspection_limit')
        analyzed, routes = [], []
        for analysis in analyses:
            if analysis.get('user_uid') != uid:
                raise ValueError('sources_changed')
            eligible = capture.get_eligible(uid, analysis['_id'])
            if eligible is None:
                analyzed.append({'analysis_id': analysis['_id'], 'eligible': False})
                continue
            if eligible.get('user_uid') != uid:
                raise ValueError('sources_changed')
            analyzed.append({'analysis_id': analysis['_id'], 'eligible': True,
                'analysis': eligible['analysis'], 'processing': eligible['processing'],
                'items': [{key: m[key] for key in ('_id', 'text', 'kind', 'status', 'evidence', 'topics') if key in m}
                          for m in eligible['memories']]})
            checks.append(lambda identifier=analysis['_id'], original=eligible:
                          capture.get_eligible(uid, identifier) == original)
        for plan in plans:
            if plan.get('user_uid') != uid:
                raise ValueError('sources_changed')
            try:
                ids = [s['record_id'] for s in plan['sources']]
                data = capture.load(uid, ids[0], ids[1:], plan['timezone'])
                valid = snapshots(data) == plan['sources']
            except CaptureError:
                valid = False
            if not valid:
                routes.append({'route_id': plan['_id'], 'sources_eligible': False})
                continue
            def recheck_plan(plan=plan):
                ids = [s['record_id'] for s in plan['sources']]
                try:
                    return snapshots(capture.load(uid, ids[0], ids[1:], plan['timezone'])) == plan['sources']
                except CaptureError:
                    return False
            checks.append(recheck_plan)
            candidate = next(({'analysis_id': a['_id'], 'candidate_id': m['_id']}
                for a in analyses for m in a.get('memories', [])
                if digest([plan['match_version'], uid, a['_id'], m['_id']]) == plan['_id']), None)
            routes.append({'route_id': plan['_id'], 'sources_eligible': True,
                'match_version': plan['match_version'], 'candidate': candidate,
                'decision': plan['decision'], 'expected_revision': plan['expected_revision'],
                'raw_record_input_ids': ids,
                'candidate_options_saved': False,
                'model_reason_saved': bool(plan['decision'].get('decision_note'))})
        job = row.get('ai_processing', {})
        memories = []
        for mid in dict.fromkeys(item['memory_id'] for item in job.get('routing', [])):
            memory = versions.get_eligible(uid, mid)
            if memory is None or memory.get('lifecycle') != 'candidate':
                memories.append({'memory_id': mid, 'eligible': False})
                continue
            memories.append({'memory_id': mid, 'eligible': True, 'revision': memory['revision'],
                'versions': [{'revision': v['revision'], 'content': v['content']} for v in memory['versions']],
                'embedding_state': memory.get('embedding_processing', {}).get('state')})
            def recheck_memory(mid=mid, fingerprint=MemoryAnswerService.dependency(memory)['fingerprint']):
                current = versions.get_eligible(uid, mid)
                return current is not None and MemoryAnswerService.dependency(current)['fingerprint'] == fingerprint
            checks.append(recheck_memory)
        current = records.find_one({'_id': row['_id'], 'user_uid': uid, 'deleted_at': None})
        if not current or any(current.get(k) != row.get(k) for k in ('content', 'created_at', 'ai_processing')):
            raise ValueError('sources_changed')
        output.append({'record_id': rid, 'content': row['content'], 'created_at': row['created_at'].isoformat(),
            'analysis_state': job.get('state'), 'analysis_error': job.get('error'),
            'saved_routing': job.get('routing', []), 'analyses': analyzed, 'route_plans': routes, 'memories': memories})
    if not all(check() for check in checks):
        raise ValueError('sources_changed')
    for row in selected:
        current = records.find_one({'_id': row['_id'], 'user_uid': uid, 'deleted_at': None})
        if not current or any(current.get(k) != row.get(k) for k in ('content', 'created_at', 'ai_processing')):
            raise ValueError('sources_changed')
    report = {'version': 'routing-inspection-v1', 'target_uid': uid, 'contains': text,
        'created_at': datetime.now(timezone.utc).isoformat(), 'records': output,
        'more_matching_records': len(rows) > limit, 'database_written': False, 'provider_calls': 0}
    if len(json.dumps(report, ensure_ascii=False).encode()) > 1024 * 1024:
        raise ValueError('inspection_limit')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contains', required=True)
    parser.add_argument('--uid')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    if not args.contains.strip() or len(args.contains) > 80 or not 1 <= args.limit <= 10:
        parser.error('--contains must have 1..80 characters and --limit must be 1..10')
    if not args.run:
        print(json.dumps({'mode': 'preview', 'database_accessed': False, 'database_written': False, 'provider_calls': 0}))
        return 0
    connection = None
    try:
        settings = get_settings(); uid = target_uid(settings, args.uid)
        if not settings.mongodb_uri:
            raise ValueError('not_configured')
        connection = DatabaseConnection(settings); connection.open()
        if connection.database is None:
            raise ValueError('database_unavailable')
        report = collect(connection.database, uid, args.contains, args.limit)
        path = PRIVATE_DIR / ('routing-inspection-' + uuid4().hex + '.json')
        checkpoint(path, report)
        print(json.dumps({'state': 'succeeded', 'report': str(path), 'record_count': len(report['records']),
                          'more_matching_records': report['more_matching_records'], 'database_written': False, 'provider_calls': 0}))
        return 0
    except Exception as exc:
        safe = {'invalid_inspection', 'inspection_limit', 'sources_changed', 'account_not_allowed',
                'not_configured', 'database_unavailable'}
        print(json.dumps({'state': 'failed', 'reason': str(exc) if str(exc) in safe else 'inspection_failed',
                          'database_written': False, 'provider_calls': 0}))
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
