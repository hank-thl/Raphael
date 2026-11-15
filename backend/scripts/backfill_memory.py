"""Preview owner records; explicitly opt in to enqueue existing capture jobs."""
import argparse
import json
from datetime import datetime, timezone

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.memory_jobs import pending_job


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Use an ISO timestamp with timezone, e.g. 2026-11-01T00:00:00+08:00') from exc


def eligible(uid):
    return {'user_uid': uid, 'deleted_at': None, 'ai_processing': {'$exists': False}}


def select_records(records, uid, limit, since=None, before=None):
    query = eligible(uid)
    if since is not None or before is not None:
        query['created_at'] = {}
        if since is not None:
            query['created_at']['$gte'] = since
        if before is not None:
            query['created_at']['$lt'] = before
    return list(records.find(query, {'_id': 1, 'content': 1, 'created_at': 1})
                .sort([('created_at', 1), ('_id', 1)]).limit(limit))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=5, help='Batch size, 1–50 (default 5)')
    parser.add_argument('--since', type=timestamp, help='Inclusive creation timestamp, with timezone')
    parser.add_argument('--before', type=timestamp, help='Exclusive creation timestamp, with timezone')
    parser.add_argument('--apply', action='store_true', help='Queue selected records; local worker performs analysis')
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 50:
        parser.error('--limit must be between 1 and 50')
    if args.since and args.before and args.since >= args.before:
        parser.error('--since must be earlier than --before')
    settings = get_settings()
    if not settings.admin_uid or not settings.mongodb_uri or not settings.mongodb_database:
        print('Backfill failed: missing owner or database configuration.')
        return 1
    # Prevent accidental queues with no enabled worker configuration.
    if args.apply and not settings.ai_capture_enabled:
        print('Backfill failed: AI_CAPTURE_ENABLED must be true for --apply.')
        return 1
    connection = DatabaseConnection(settings)
    queued = skipped = 0
    stage = 'selection'
    try:
        connection.open()
        if connection.database is None:
            print('Backfill failed: database unavailable.')
            return 1
        records = connection.database['records']
        rows = select_records(records, settings.admin_uid, args.limit, args.since, args.before)
        print(json.dumps({'mode': 'apply' if args.apply else 'preview', 'selected_count': len(rows),
                          'records': [{'record_id': str(row['_id']),
                                       'created_at': row['created_at'].isoformat(),
                                       'content_preview': row['content'][:80]} for row in rows]}, ensure_ascii=False))
        if not args.apply:
            print('Preview only: no writes or Gemini calls. Add --apply to queue this batch selection.')
            return 0
        stage = 'enqueue'
        for row in rows:
            # Recheck ownership, deletion and job absence atomically, including races.
            query = {**eligible(settings.admin_uid), '_id': row['_id']}
            result = records.update_one(query, {'$set': {'ai_processing': pending_job()}})
            if result.matched_count:
                queued += 1
            else:
                skipped += 1
        print(json.dumps({'queued_count': queued, 'skipped_count': skipped}))
        print('Queued only. Run the local backend worker to analyze. Repeating --apply selects the next eligible batch.')
        return 0
    except Exception:
        # Driver errors may contain credentials. An interrupted write has unknown outcome.
        print(json.dumps({'error': 'storage_or_validation_failed', 'stage': stage,
                          'confirmed_queued_count': queued, 'skipped_count': skipped,
                          'write_outcome_may_be_unknown': stage == 'enqueue'}))
        return 1
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
