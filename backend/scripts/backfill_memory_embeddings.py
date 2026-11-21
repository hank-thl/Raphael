"""Bounded backfill for explicitly allowed AI accounts. --run previews; --run --save writes."""
import argparse
import json
import re

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.analyses import AnalysesRepository
from app.repositories.memories import MemoriesRepository
from app.repositories.memory_embeddings import MemoryEmbeddingsRepository
from app.services.embeddings import EmbeddingError, GeminiEmbedder, PROFILE
from app.services.memory_capture import MemoryCaptureService
from app.services.memory_embeddings import MemoryEmbeddingError, MemoryEmbeddingService
from app.services.memory_versions import MemoryVersionsService

MAX_BATCH = 20


class CountedEmbedder:
    def __init__(self, provider, budget):
        self.provider, self.budget, self.attempts = provider, budget, 0

    def document(self, text):
        if self.attempts >= self.budget:
            raise MemoryEmbeddingError('call_budget_exceeded')
        self.attempts += 1
        return self.provider.document(text)


def target_uid(settings, requested):
    uid = requested or settings.admin_uid
    allowed = {settings.admin_uid} | (settings.atlas_chat_allowed_uids & settings.ai_user_uids)
    if not uid or uid not in allowed:
        raise MemoryEmbeddingError('account_not_allowed')
    return uid


def plan_batch(reader, service, uid, limit, after=None):
    if not uid or type(limit) is not int or not 1 <= limit <= MAX_BATCH:
        raise MemoryEmbeddingError('invalid_batch')
    query = {'user_uid': uid, 'lifecycle': 'candidate'}
    if after is not None:
        if not re.fullmatch('[a-f0-9]{64}', after):
            raise MemoryEmbeddingError('invalid_cursor')
        query['_id'] = {'$gt': after}
    rows = list(reader.memories.collection.find(query).sort([('_id', 1)]).limit(limit + 1))
    items = []
    for row in rows[:limit]:
        if row.get('user_uid') != uid:
            raise MemoryEmbeddingError('owner_mismatch')
        mid = row['_id']
        try:
            metadata, _ = service.snapshot(uid, mid)
            cached = service.current(uid, mid)
            service.recheck(uid, mid, metadata)
            items.append({'memory_id': mid, 'action': 'reuse' if cached else 'generate', 'expected': metadata})
        except (MemoryEmbeddingError, EmbeddingError) as exc:
            reason = exc.reason if isinstance(exc, EmbeddingError) else str(exc)
            if reason not in {'memory_unavailable', 'input_limit'}:
                raise
            items.append({'memory_id': mid, 'action': 'skip', 'reason': reason})
    return {'items': items, 'more_memories_outside_batch': len(rows) > limit,
            'next_after_memory_id': rows[limit - 1]['_id'] if len(rows) > limit else None}


def public_plan(plan):
    return {**plan, 'items': [{k: v for k, v in row.items() if k != 'expected'} for row in plan['items']],
            'planned_provider_calls_max': sum(row['action'] == 'generate' for row in plan['items'])}


def save_batch(service, uid, plan):
    for item in plan['items']:
        if item['action'] == 'skip':
            yield {'memory_id': item['memory_id'], 'state': 'skipped', 'reason': item['reason']}
            continue
        # A changed record is never silently embedded under a previously inspected plan.
        service.recheck(uid, item['memory_id'], item['expected'])
        _, reused = service.save(uid, item['memory_id'], expected_snapshot=item['expected'])
        yield {'memory_id': item['memory_id'], 'state': 'reused' if reused else 'saved'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--save', action='store_true')
    parser.add_argument('--uid', help='Defaults to admin; other UIDs must be in both AI and Atlas allowlists.')
    parser.add_argument('--after', help='Continue after next_after_memory_id from a completed batch.')
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= MAX_BATCH:
        parser.error('--limit must be between 1 and 20')
    if args.save and not args.run:
        parser.error('--save requires --run')
    if args.after and not re.fullmatch('[a-f0-9]{64}', args.after):
        parser.error('--after must be a lowercase 64-character hex memory ID')
    base = {'profile': PROFILE, 'limit': args.limit, 'automatic_retry': False, 'after': args.after}
    if not args.run:
        print(json.dumps({**base, 'mode': 'offline_preview', 'database_accessed': False,
            'database_written': False, 'provider_calls': 0, 'planned_provider_calls_max': args.limit}))
        return 0
    connection, counted = None, None
    completed = []
    write_phase_started = False
    try:
        settings = get_settings()
        if not settings.admin_uid or not settings.mongodb_uri or (args.save and not settings.gemini_api_key):
            raise MemoryEmbeddingError('not_configured')
        uid = target_uid(settings, args.uid)
        base['target_uid'] = uid
        connection = DatabaseConnection(settings)
        connection.open()
        db = connection.database
        if db is None:
            raise MemoryEmbeddingError('database_unavailable')
        reader = MemoryVersionsService(MemoryCaptureService(db['records'], AnalysesRepository(db['analyses']), None, ''),
                                       MemoriesRepository(db['memories']))
        counted = CountedEmbedder(GeminiEmbedder(settings.gemini_api_key), args.limit)
        service = MemoryEmbeddingService(reader, MemoryEmbeddingsRepository(db['memory_embeddings']), counted)
        plan = plan_batch(reader, service, uid, args.limit, args.after)
        if args.save:
            write_phase_started = True
            for result in save_batch(service, uid, plan):
                completed.append(result)
        print(json.dumps({**base, **public_plan(plan), 'state': 'succeeded',
            'mode': 'save' if args.save else 'readonly_preview', 'completed': completed,
            'provider_call_attempts': counted.attempts,
            'database_written': any(r['state'] == 'saved' for r in completed)}))
        return 0
    except Exception as exc:
        reason = exc.reason if isinstance(exc, EmbeddingError) else str(exc) if isinstance(exc, MemoryEmbeddingError) else 'storage_or_validation_failed'
        print(json.dumps({**base, 'state': 'failed', 'reason': reason, 'completed': completed,
            'provider_call_attempts': counted.attempts if counted else 0,
            'database_write_may_have_occurred': write_phase_started}))
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
