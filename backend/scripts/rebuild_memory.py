"""Replace the configured owner's derived AI data and requeue analyzed source records."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from bson import ObjectId, json_util
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.memory_jobs import pending_job

COLLECTIONS = ('analyses', 'memories', 'content_types', 'memory_routes')
MAX_DOCUMENTS = 10000
BACKUP_DIR = Path(__file__).resolve().parents[2] / 'data' / 'private' / 'memory-rebuild'


class RebuildError(ValueError):
    pass


def collect(db, uid, max_records, session=None):
    if not uid:
        raise RebuildError('owner_required')
    scope = {'user_uid': uid}
    derived = {}
    for name in COLLECTIONS:
        derived[name] = list(db[name].find(scope, session=session).limit(MAX_DOCUMENTS + 1))
        if len(derived[name]) > MAX_DOCUMENTS:
            raise RebuildError('derived_document_limit')
    source_ids = set()
    for analysis in derived['analyses']:
        if analysis.get('document_type') == 'topic_summary':
            continue
        for identifier in [analysis.get('target_record_id'), *analysis.get('context_record_ids', [])]:
            if identifier is not None:
                if not ObjectId.is_valid(identifier):
                    raise RebuildError('invalid_analysis_source')
                source_ids.add(ObjectId(identifier))
    query = {**scope, 'deleted_at': None, '$or': [
        {'ai_processing': {'$exists': True}}, {'_id': {'$in': list(source_ids)}}]}
    rows = list(db['records'].find(query, session=session).sort(
        [('created_at', 1), ('_id', 1)]).limit(max_records + 1))
    if len(rows) > max_records:
        raise RebuildError('record_limit')
    # A stopped worker may still have a stored running lease; do not race it.
    if any(row.get('ai_processing', {}).get('state') == 'running' for row in rows):
        raise RebuildError('running_jobs_present')
    if not rows:
        raise RebuildError('no_analyzed_active_records')
    return {'owner_uid': uid, 'derived': derived, 'records': rows}


def save_backup(snapshot):
    # Local only, gitignored, private permissions, no clobbering an earlier backup.
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    filename = BACKUP_DIR / f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}.json'
    descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(json_util.dumps({'backup_version': 'memory-rebuild-v1', **snapshot}))
        stream.flush()
        os.fsync(stream.fileno())
    return filename


def replace(db, snapshot, session):
    uid = snapshot['owner_uid']
    for name in COLLECTIONS:
        db[name].delete_many({'user_uid': uid}, session=session)
    identifiers = [row['_id'] for row in snapshot['records']]
    result = db['records'].update_many(
        {'user_uid': uid, 'deleted_at': None, '_id': {'$in': identifiers}},
        {'$set': {'ai_processing': pending_job()}}, session=session)
    if result.matched_count != len(identifiers):
        raise RebuildError('sources_changed')


def describe(snapshot):
    rows = snapshot['records']
    return {'record_count': len(rows),
            'replace_counts': {key: len(value) for key, value in snapshot['derived'].items()},
            'oldest_created_at': rows[0]['created_at'].isoformat(),
            'newest_created_at': rows[-1]['created_at'].isoformat()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Back up, replace owner AI results, and queue fresh analyses')
    parser.add_argument('--max-records', type=int, default=500, help='Refuse larger rebuilds, never truncate (1–2000)')
    args = parser.parse_args(argv)
    if not 1 <= args.max_records <= 2000:
        parser.error('--max-records must be between 1 and 2000')
    settings = get_settings()
    if not settings.admin_uid or not settings.mongodb_uri or not settings.mongodb_database:
        print(json.dumps({'error': 'owner_or_database_not_configured'}))
        return 1
    if args.apply and (not settings.gemini_api_key or not settings.gemini_model or not settings.ai_capture_enabled):
        print(json.dumps({'error': 'capture_not_configured'}))
        return 1
    connection = DatabaseConnection(settings)
    backup = None
    committing = False
    try:
        connection.open()
        if connection.database is None:
            raise RebuildError('database_unavailable')
        db = connection.database
        if not args.apply:
            snapshot = collect(db, settings.admin_uid, args.max_records)
            print(json.dumps({'mode': 'preview', **describe(snapshot), 'database_written': False}))
            return 0
        # No model calls or automatic transaction retry. All owner resets commit together.
        with connection.client.start_session() as session:
            session.start_transaction(read_concern=ReadConcern('snapshot'), write_concern=WriteConcern('majority'))
            try:
                snapshot = collect(db, settings.admin_uid, args.max_records, session)
                backup = save_backup(snapshot)
                replace(db, snapshot, session)
                committing = True
                session.commit_transaction()
                committing = False
            except Exception:
                if session.in_transaction:
                    session.abort_transaction()
                raise
        print(json.dumps({'mode': 'requeued', **describe(snapshot), 'backup': str(backup),
                          'database_written': True, 'analysis_complete': False}, ensure_ascii=False))
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, RebuildError) else 'storage_or_backup_failed'
        print(json.dumps({'error': reason, 'backup': str(backup) if backup else None,
                          'commit_outcome_may_be_unknown': committing}, ensure_ascii=False))
        return 1
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
