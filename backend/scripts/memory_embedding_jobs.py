"""Inspect one embedding job or explicitly retry it; no embedding calls in this CLI."""
import argparse
import json
import re

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.analyses import AnalysesRepository
from app.repositories.memories import MemoriesRepository
from app.repositories.embedding_jobs import EmbeddingJobsRepository
from app.services.memory_capture import MemoryCaptureService
from app.services.memory_embeddings import MemoryEmbeddingError
from app.services.memory_versions import MemoryVersionsService
from scripts.backfill_memory_embeddings import target_uid


def inspect_job(db, uid, memory_id=None, retry=False):
    collection = db['memories']
    if memory_id:
        row = collection.find_one({'_id': memory_id, 'user_uid': uid, 'lifecycle': 'candidate'})
    else:
        rows = list(collection.find({'user_uid': uid, 'lifecycle': 'candidate',
            'embedding_processing': {'$exists': True}}).sort([('updated_at', -1), ('_id', -1)]).limit(1))
        row = rows[0] if rows else None
    if not row or row.get('user_uid') != uid:
        raise MemoryEmbeddingError('job_not_found')
    jobs = EmbeddingJobsRepository(collection)
    if retry:
        reader = MemoryVersionsService(MemoryCaptureService(db['records'], AnalysesRepository(db['analyses']), None, ''),
                                       MemoriesRepository(collection))
        if not reader.get_eligible_many(uid, [row]):
            raise MemoryEmbeddingError('memory_unavailable')
        jobs.expire(uid)
        row = jobs.retry(uid, row['_id'], row['revision'])
        if row is None:
            raise MemoryEmbeddingError('job_not_retryable')
    job = row.get('embedding_processing', {})
    return {'memory_id': row['_id'], 'revision': row['revision'],
            'job': {'state': job.get('state', 'not_requested'),
                    **{key: job.get(key) for key in ('revision', 'profile', 'attempts', 'error', 'reused')}},
            'database_written': retry}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uid')
    parser.add_argument('--memory-id')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--retry', action='store_true')
    args = parser.parse_args(argv)
    if args.retry and not args.run:
        parser.error('--retry requires --run')
    if args.memory_id and not re.fullmatch('[a-f0-9]{64}', args.memory_id):
        parser.error('--memory-id must be a lowercase 64-character hex ID')
    if not args.run:
        print(json.dumps({'mode': 'preview', 'database_accessed': False, 'database_written': False, 'provider_calls': 0}))
        return 0
    connection = None
    try:
        settings = get_settings()
        uid = target_uid(settings, args.uid)
        if not settings.mongodb_uri:
            raise MemoryEmbeddingError('not_configured')
        connection = DatabaseConnection(settings)
        connection.open()
        if connection.database is None:
            raise MemoryEmbeddingError('database_unavailable')
        result = inspect_job(connection.database, uid, args.memory_id, args.retry)
        print(json.dumps({'state': 'succeeded', 'target_uid': uid, 'provider_calls': 0, **result}))
        return 0
    except Exception as exc:
        print(json.dumps({'state': 'failed', 'reason': str(exc) if isinstance(exc, MemoryEmbeddingError)
                          else 'storage_unavailable', 'provider_calls': 0, 'automatic_retry': False,
                          'database_write_may_have_occurred': args.retry}))
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
