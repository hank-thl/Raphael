"""Offline preview by default; --run checks an existing index and makes at most one query embedding call."""
import argparse
import json
from time import perf_counter

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.analyses import AnalysesRepository
from app.repositories.memories import MemoriesRepository
from app.repositories.memory_embeddings import MemoryEmbeddingsRepository
from app.services.embeddings import EmbeddingError, GeminiEmbedder, PROFILE
from app.services.memory_capture import MemoryCaptureService
from app.services.memory_embeddings import MemoryEmbeddingError, MemoryEmbeddingService
from app.services.memory_search import INDEX_NAME, MemorySearchError, MemorySearchService
from app.services.memory_versions import MemoryVersionsService

from app.services.vector_index import INDEX_FILE, check_index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--question', default='我看完 ACT 的書後有什麼感受？')
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    report = {'index': INDEX_NAME, 'collection': 'memory_embeddings', 'profile': PROFILE,
        'search_mode': 'exact', 'database_written': False, 'automatic_retry': False,
        'planned_provider_calls_max': 1}
    if not args.run:
        print(json.dumps({**report, 'mode': 'preview', 'provider_calls': 0, 'database_accessed': False,
                          'index_definition_file': str(INDEX_FILE)}))
        return 0
    connection = None
    started = perf_counter()
    try:
        settings = get_settings()
        if not all((settings.admin_uid, settings.mongodb_uri, settings.gemini_api_key)):
            raise MemorySearchError('not_configured')
        connection = DatabaseConnection(settings)
        connection.open()
        db = connection.database
        if db is None:
            raise MemorySearchError('database_unavailable')
        collection = db['memory_embeddings']
        check_index(collection)  # Do not spend a query call on a missing/building index.
        reader = MemoryVersionsService(MemoryCaptureService(db['records'], AnalysesRepository(db['analyses']), None, ''),
                                       MemoriesRepository(db['memories']))
        embedder = GeminiEmbedder(settings.gemini_api_key)
        embeddings = MemoryEmbeddingService(reader, MemoryEmbeddingsRepository(collection), embedder)
        result = MemorySearchService(collection, embeddings, embedder).search(settings.admin_uid, args.question)
        print(json.dumps({**report, 'state': 'succeeded', **result,
                          'elapsed_ms': round((perf_counter() - started) * 1000)}))
        return 0
    except Exception as exc:
        reason = exc.reason if isinstance(exc, EmbeddingError) else str(exc) if isinstance(exc, (MemorySearchError, MemoryEmbeddingError)) else 'search_or_validation_failed'
        print(json.dumps({**report, 'state': 'failed', 'reason': reason}))
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
