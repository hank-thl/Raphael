"""Small persistent single-thread worker; failed jobs require explicit retry."""
import logging
from threading import Event, Thread

from app.repositories.analyses import AnalysesRepository
from app.repositories.content_types import ContentTypesRepository
from app.repositories.memories import MemoriesRepository
from app.repositories.memory_jobs import MemoryJobsRepository
from app.repositories.embedding_jobs import EmbeddingJobsRepository
from app.repositories.memory_embeddings import MemoryEmbeddingsRepository
from app.services.embedding_worker import process_embedding_job
from app.services.embeddings import GeminiEmbedder
from app.services.memory_embeddings import MemoryEmbeddingService
from app.services.classification import ClassificationService
from app.services.gemini import GeminiError, GeminiExtractor
from app.services.memory_capture import CaptureError, MemoryCaptureService
from app.services.memory_matching import GeminiMemoryMatcher
from app.services.memory_routing import MemoryRoutingService
from app.services.memory_versions import MemoryVersionsService
from app.services.topics import MemoryTopics

logger = logging.getLogger(__name__)


def build_pipeline(db, settings):
    extractor = GeminiExtractor(settings.gemini_api_key, settings.gemini_model)
    capture = MemoryCaptureService(db['records'], AnalysesRepository(db['analyses']),
        ClassificationService(extractor, ContentTypesRepository(db['content_types'])), settings.gemini_model)
    versions = MemoryVersionsService(capture, MemoriesRepository(db['memories']))
    capture.classifier.topics = MemoryTopics(versions)
    router = MemoryRoutingService(capture, versions, GeminiMemoryMatcher(extractor), AnalysesRepository(db['memory_routes']))
    return capture, router


def process_job(jobs, capture, router, row, stopped=lambda: False, authorized=lambda: True):
    def checkpoint():
        if stopped():
            raise CaptureError('interrupted')
        if not authorized():
            raise CaptureError('access_revoked')
        if not jobs.touch(row):
            raise CaptureError('job_no_longer_active')
    try:
        checkpoint()
        # Do not send recent raw messages indiscriminately. The matcher supplies
        # authorized historical memory evidence only after standalone extraction.
        document, _ = capture.capture(row['user_uid'], str(row['_id']))
        outcomes = []
        for candidate in document['memories']:
            checkpoint()
            outcomes.append(router.route(row['user_uid'], document['_id'], candidate['_id']))
        checkpoint()
        jobs.finish(row, 'succeeded', analysis_id=document['_id'], routing=outcomes)
    except (GeminiError, CaptureError) as exc:
        # Do not store provider bodies, exception messages or private text.
        known = {'access_revoked', 'interrupted', 'source_unavailable', 'sources_changed', 'revision_conflict',
                 'matching_context_limit', 'not_configured', 'rate_limited', 'read_timeout', 'connect_timeout'}
        reason = exc.reason if isinstance(exc, GeminiError) else str(exc)
        jobs.finish(row, 'failed', error=reason if reason in known else 'analysis_failed')
    except Exception:
        jobs.finish(row, 'failed', error='analysis_failed')


class MemoryWorker:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self.stop_event = Event()
        self.thread = Thread(target=self.run, name='raphael-memory-worker', daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            logger.warning('Memory worker shutdown pending; interrupted lease will require manual retry')

    def run(self):
        jobs = MemoryJobsRepository(self.db['records'])
        capture, router = build_pipeline(self.db, self.settings)
        embedding_jobs, embedding_service = None, None
        if self.settings.memory_embedding_enabled and self.settings.embedding_user_uids:
            embedding_jobs = EmbeddingJobsRepository(self.db['memories'])
            embedding_service = MemoryEmbeddingService(router.versions,
                MemoryEmbeddingsRepository(self.db['memory_embeddings']), GeminiEmbedder(self.settings.gemini_api_key))
        while not self.stop_event.is_set():
            processed = False
            # One job per UID each round prevents a busy user starving others.
            for uid in sorted(self.settings.ai_user_uids):
                if self.stop_event.is_set():
                    break
                try:
                    if self.settings.ai_capture_enabled:
                        jobs.expire(uid)
                    if not self.account_enabled(uid):
                        continue
                    if self.settings.ai_capture_enabled:
                        row = jobs.claim(uid)
                        if row is not None:
                            process_job(jobs, capture, router, row, self.stop_event.is_set,
                                        authorized=lambda uid=uid: self.account_enabled(uid))
                            processed = True
                    if (embedding_jobs is not None and uid in self.settings.embedding_user_uids
                            and not self.stop_event.is_set()):
                        try:
                            embedding_jobs.expire(uid)
                            memory = embedding_jobs.claim(uid)
                            if memory is not None:
                                process_embedding_job(embedding_jobs, embedding_service, memory, self.stop_event.is_set,
                                    authorized=lambda uid=uid: self.account_enabled(uid))
                                processed = True
                        except Exception:
                            logger.warning('Embedding worker unavailable; pending jobs remain stored')
                except Exception:
                    logger.warning('Memory worker unavailable; pending jobs remain stored')
            if not processed:
                self.stop_event.wait(3)

    def account_enabled(self, uid):
        if uid not in self.settings.ai_user_uids:
            return False
        if uid == self.settings.admin_uid:
            return True
        entry = self.db['access_users'].find_one({'_id': uid})
        return entry.get('enabled') is True if entry is not None else uid in self.settings.allowed_uids
