"""One document embedding attempt per persisted job; failures never undo Memory capture."""
from app.services.embeddings import EmbeddingError
from app.services.memory_embeddings import MemoryEmbeddingError, MemoryEmbeddingService


def process_embedding_job(jobs, service, row, stopped=lambda: False, authorized=lambda: True):
    def checkpoint():
        if stopped():
            raise MemoryEmbeddingError('interrupted')
        if not authorized():
            raise MemoryEmbeddingError('access_revoked')
        if not jobs.touch(row):
            raise MemoryEmbeddingError('job_no_longer_active')

    try:
        checkpoint()
        expected, _ = MemoryEmbeddingService.metadata(row)
        # A claim is bound to that revision, never silently vectorize a newer one.
        saved, reused = service.save(row['user_uid'], row['_id'], expected_snapshot=expected,
                                     checkpoint=checkpoint)
        checkpoint()
        jobs.finish(row, 'succeeded', cache_id=saved['_id'], reused=reused)
    except (EmbeddingError, MemoryEmbeddingError) as exc:
        reason = exc.reason if isinstance(exc, EmbeddingError) else str(exc)
        known = {'interrupted', 'access_revoked', 'job_no_longer_active', 'sources_changed',
            'memory_unavailable', 'input_limit', 'invalid_input', 'invalid_cache', 'embedding_mismatch',
            'not_configured', 'rate_limited', 'connect_timeout', 'read_timeout', 'timeout',
            'network_error', 'configuration_rejected', 'provider_unavailable', 'invalid_vector', 'invalid_response'}
        jobs.finish(row, 'failed', error=reason if reason in known else 'embedding_failed')
    except Exception:
        jobs.finish(row, 'failed', error='embedding_failed')
