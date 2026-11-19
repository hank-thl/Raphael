"""Export the configured owner's eligible memories locally; no DB writes or LLM calls."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.analyses import AnalysesRepository
from app.repositories.memories import MemoriesRepository
from app.services.memory_answer import MemoryAnswerService
from app.services.memory_capture import MemoryCaptureService
from app.services.memory_versions import MemoryVersionsService

PRIVATE_DIR = Path(__file__).resolve().parents[2] / 'data/private/retrieval-review'


def collect(reader, uid, limit=500):
    if not uid:
        raise ValueError('owner_required')
    if not 1 <= limit <= 500:
        raise ValueError('invalid_limit')
    rows = list(reader.memories.recent(uid).limit(limit + 1))
    if len(rows) > limit:
        raise ValueError('memory_limit')
    eligible = reader.get_eligible_many(uid, rows)
    # A local export is a snapshot, not continuing authorization to use sources.
    # Re-read selected IDs so concurrent version/source changes abort the export.
    current = list(reader.memories.collection.find({
        'user_uid': uid, 'lifecycle': 'candidate', '_id': {'$in': [r['_id'] for r in eligible]}}))
    checked = reader.get_eligible_many(uid, current)
    if MemoryAnswerService.fingerprint(eligible) != MemoryAnswerService.fingerprint(checked):
        raise ValueError('sources_changed')
    items = []
    for row in eligible:
        if row['user_uid'] != uid:
            raise ValueError('owner_mismatch')
        def version(v):
            return {'revision': v['revision'], 'content': v['content']}
        items.append({'memory_id': row['_id'], 'revision': row['revision'],
                      'fingerprint': MemoryAnswerService.dependency(row)['fingerprint'],
                      'recorded_memory_created_at': row['created_at'].isoformat(),
                      'memory_updated_at': row['updated_at'].isoformat(),
                      'latest': version(row['versions'][-1]),
                      'history': [version(v) for v in row['versions'][:-1]]})
    return {'export_version': 'memory-retrieval-review-v1',
            'exported_at': datetime.now(timezone.utc).isoformat(),
            'scope': 'configured_admin_only', 'scanned_count': len(rows),
            'eligible_count': len(items), 'excluded_count': len(rows) - len(items),
            'snapshot_only': True, 'database_written': False, 'provider_calls': 0,
            'memories': items}


def save_private(data):
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = PRIVATE_DIR / f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}.json'
    encoded = json.dumps(data, ensure_ascii=False, indent=2)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        output.write(encoded + '\n')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, choices=range(1, 501), default=500, metavar='1..500')
    args = parser.parse_args(argv)
    settings = get_settings()
    if not settings.admin_uid or not settings.mongodb_uri or not settings.mongodb_database:
        print('Export unavailable: owner/database configuration missing.')
        return 1
    connection = DatabaseConnection(settings)
    try:
        connection.open()
        if connection.database is None:
            print('Export unavailable: database not initialized.')
            return 1
        db = connection.database
        reader = MemoryVersionsService(
            MemoryCaptureService(db['records'], AnalysesRepository(db['analyses']), None, ''),
            MemoriesRepository(db['memories']))
        data = collect(reader, settings.admin_uid, args.limit)
        path = save_private(data)
        print(json.dumps({'eligible_count': data['eligible_count'], 'excluded_count': data['excluded_count'],
                          'file': str(path), 'database_written': False, 'provider_calls': 0}, ensure_ascii=False))
        return 0
    except Exception as exc:
        reason = str(exc) if type(exc) is ValueError and str(exc) in {
            'memory_limit', 'sources_changed', 'owner_mismatch', 'owner_required', 'invalid_limit'} else 'read_or_export_failed'
        print(f'Export failed: {reason}. No complete snapshot confirmed.')
        return 1
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
