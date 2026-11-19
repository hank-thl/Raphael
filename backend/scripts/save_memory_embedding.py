"""One administrator-owned Memory only. Default offline preview; --run inspects, --save writes."""
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--memory-id', required=True)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--save', action='store_true')
    args = parser.parse_args(argv)
    if not re.fullmatch('[a-f0-9]{64}', args.memory_id):
        parser.error('--memory-id must be a 64-character lowercase hex ID')
    if args.save and not args.run:
        parser.error('--save requires --run')
    report = {'profile': PROFILE, 'memory_id': args.memory_id, 'collection': 'memory_embeddings',
              'planned_provider_calls_max': int(args.save), 'automatic_retry': False}
    if not args.run:
        print(json.dumps({**report, 'mode': 'preview', 'database_accessed': False, 'database_written': False}))
        return 0
    connection = None
    # An acknowledged insert can precede a subsequent source-check failure.
    write_attempted = False
    try:
        settings = get_settings()
        if not settings.admin_uid or not settings.mongodb_uri or (args.save and not settings.gemini_api_key):
            raise MemoryEmbeddingError('not_configured')
        connection = DatabaseConnection(settings)
        connection.open()
        db = connection.database
        if db is None:
            raise MemoryEmbeddingError('database_unavailable')
        reader = MemoryVersionsService(MemoryCaptureService(db['records'], AnalysesRepository(db['analyses']), None, ''),
                                       MemoriesRepository(db['memories']))
        service = MemoryEmbeddingService(reader, MemoryEmbeddingsRepository(db['memory_embeddings']),
                                         GeminiEmbedder(settings.gemini_api_key))
        cached = service.current(settings.admin_uid, args.memory_id)
        reused = cached is not None
        if args.save and cached is None:
            write_attempted = True
            cached, reused = service.save(settings.admin_uid, args.memory_id)
        print(json.dumps({**report, 'mode': 'save' if args.save else 'inspect', 'state': 'succeeded',
            'current_vector_available': cached is not None, 'reused': reused,
            'revision': cached['revision'] if cached else None, 'database_written': bool(args.save and not reused)}))
        return 0
    except Exception as exc:
        reason = exc.reason if isinstance(exc, EmbeddingError) else str(exc) if isinstance(exc, MemoryEmbeddingError) else 'storage_or_validation_failed'
        print(json.dumps({**report, 'state': 'failed', 'reason': reason,
                          'database_write_may_have_occurred': write_attempted}))
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
