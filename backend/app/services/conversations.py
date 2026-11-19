"""Save dialogue independently; never feed it to the memory capture worker."""
import logging
from itertools import islice

from app.repositories.records import utc_now
from app.schemas.memory_answer import MemoryAnswer
from app.services.gemini import GeminiError
from app.services.memory_answer import ANSWER_PROMPT_VERSION, ANSWER_VERSION, AnswerError, MemoryAnswerService


logger = logging.getLogger(__name__)


class ConversationService:
    def __init__(self, repository, answerer):
        self.repository, self.answerer = repository, answerer

    def public(self, uid, turns):
        identifiers = {item['memory_id'] for row in turns if row['state'] == 'succeeded' for item in row['inputs']}
        current = {}
        if identifiers:
            reader = self.answerer.reader
            rows = iter(reader.memories.collection.find({'user_uid': uid, 'lifecycle': 'candidate',
                                                        '_id': {'$in': list(identifiers)}}))
            while batch := list(islice(rows, 20)):
                for memory in reader.get_eligible_many(uid, batch):
                    current[memory['_id']] = memory
        result = []
        for row in turns:
            if row['user_uid'] != uid:
                continue
            state, answer, error = row['state'], None, row.get('error')
            if state == 'running' and row['lease_until'] <= utc_now():
                state, error = 'failed', 'interrupted'
            if state == 'succeeded':
                try:
                    dependencies = row['inputs']
                    if not all(item['memory_id'] in current and
                               MemoryAnswerService.dependency(current[item['memory_id']])['fingerprint'] == item['fingerprint']
                               for item in dependencies):
                        raise ValueError('sources_changed')
                    parsed = MemoryAnswer.model_validate(row['answer'])
                    parsed.validate_sources([current[item['memory_id']] for item in dependencies])
                    answer = parsed.model_dump()
                except (ValueError, TypeError, KeyError):
                    state, error = 'invalid', 'sources_changed'
            retrieval_mode = None
            if state == 'succeeded' and answer is not None:
                processing = row.get('processing')
                retrieval = processing.get('retrieval') if isinstance(processing, dict) else None
                if isinstance(retrieval, dict) and retrieval.get('mode') in ('atlas', 'direct'):
                    retrieval_mode = retrieval['mode']
            result.append({'id': row['_id'], 'request_id': row['request_id'], 'question': row['question'],
                           'state': state, 'answer': answer, 'error': error,
                           'retrieval_mode': retrieval_mode,
                           'created_at': row['created_at'], 'updated_at': row['updated_at']})
        return result

    def ask(self, uid, request_id, question):
        row, created = self.repository.start(uid, request_id, question)
        if not created:
            return self.public(uid, [row])[0]
        try:
            previous = [old for old in self.repository.page(uid, limit=3, cursor=(row['created_at'], row['_id']))
                        if old['_id'] != row['_id'] and old['state'] == 'succeeded'][:3]
            generated = self.answerer.generate(uid, question, previous)
            processing = {'provider': 'gemini', 'model': self.answerer.extractor.model,
                          'version': ANSWER_VERSION, 'prompt_version': ANSWER_PROMPT_VERSION}
            retrieval = generated.get('retrieval', {'mode': 'direct'})
            if isinstance(retrieval, dict):
                processing['retrieval'] = retrieval
            finished = self.repository.finish(row, 'succeeded', answer=generated['answer'].model_dump(),
                inputs=generated['inputs'], processing=processing)
        except (GeminiError, AnswerError) as exc:
            reason = str(exc) if isinstance(exc, AnswerError) else exc.reason
            known = {'input_limit', 'sources_changed', 'invalid_answer', 'invalid_question',
                     'owner_required', 'retrieval_unavailable', 'retrieval_incomplete',
                     'rate_limited', 'not_configured', 'configuration_rejected',
                     'provider_unavailable', 'response_too_large', 'connect_timeout', 'read_timeout',
                     'write_timeout', 'pool_timeout', 'timeout', 'network_error', 'blocked',
                     'missing_candidate', 'incomplete_or_blocked', 'invalid_response'}
            diagnostic = reason if reason in known else 'unknown_error'
            stage = 'provider' if isinstance(exc, GeminiError) else 'answer_validation'
            if reason in {'retrieval_unavailable', 'retrieval_incomplete'}:
                stage = 'retrieval'
            status = getattr(exc, 'status_code', None)
            logger.warning('Conversation answer failed stage=%s reason=%s status=%s',
                           stage, diagnostic, status if type(status) is int else None)
            safe = reason if reason in {'input_limit', 'sources_changed', 'rate_limited'} else 'generation_failed'
            finished = self.repository.finish(row, 'failed', error=safe)
        if finished is None:
            finished = self.repository.get(uid, request_id)
        return self.public(uid, [finished])[0]
