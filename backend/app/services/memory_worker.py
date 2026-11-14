"""Small persistent single-thread worker; failed jobs require explicit retry."""
import logging
from threading import Event, Thread

from app.repositories.analyses import AnalysesRepository
from app.repositories.content_types import ContentTypesRepository
from app.repositories.memories import MemoriesRepository
from app.repositories.memory_jobs import MemoryJobsRepository
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


def process_job(jobs, capture, router, row, stopped=lambda: False):
    def checkpoint():
        if stopped():
            raise CaptureError('interrupted')
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
        known = {'interrupted', 'source_unavailable', 'sources_changed', 'revision_conflict',
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
        while not self.stop_event.is_set():
            try:
                jobs.expire(self.settings.admin_uid)
                row = jobs.claim(self.settings.admin_uid)
                if row is not None:
                    process_job(jobs, capture, router, row, self.stop_event.is_set)
                    continue
            except Exception:
                logger.warning('Memory worker unavailable; pending jobs remain stored')
            self.stop_event.wait(3)
