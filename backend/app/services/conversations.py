"""Save dialogue independently; never feed it to the memory capture worker."""
import logging
import json
from itertools import islice
from bson import ObjectId

from app.repositories.records import utc_now
from app.schemas.memory_answer import MemoryAnswer
from app.services.gemini import GeminiError
from app.services.memory_answer import ANSWER_PROMPT_VERSION, ANSWER_VERSION, AnswerError, MemoryAnswerService
from app.services.memory_capture import digest


logger = logging.getLogger(__name__)


def record_fingerprint(row):
    return digest([row['content'], row['created_at'].isoformat()])


class ConversationService:
    def __init__(self, repository, answerer):
        self.repository, self.answerer = repository, answerer

    def records_valid(self, uid, row, sources=None):
        """Fresh reads: deleted raw chat context must never survive as prior dialogue."""
        refs = row.get('record_inputs', [])
        if not refs:
            return row.get('source_record_id') is None
        records = self.answerer.reader.capture.records
        for ref in refs:
            source = (sources.get(ref['record_id']) if sources is not None else
                      records.find_one({'_id': ObjectId(ref['record_id']), 'user_uid': uid, 'deleted_at': None}))
            if source is None or record_fingerprint(source) != ref['fingerprint']:
                return False
        return True

    def public(self, uid, turns):
        raw_ids = {ref['record_id'] for row in turns if row.get('user_uid') == uid
                   for ref in row.get('record_inputs', [])}
        raw_sources = {str(row['_id']): row for row in self.answerer.reader.capture.records.find({
            'user_uid': uid, 'deleted_at': None, '_id': {'$in': [ObjectId(key) for key in raw_ids]}})} if raw_ids else {}
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
            if not self.records_valid(uid, row, raw_sources):
                state, error = 'invalid', 'sources_changed'
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
            question = row['question'] if not row.get('source_record_id') or row['source_record_id'] in raw_sources else ''
            result.append({'id': row['_id'], 'request_id': row['request_id'], 'question': question,
                           'state': state, 'answer': answer, 'error': error,
                           'retrieval_mode': retrieval_mode,
                           'created_at': row['created_at'], 'updated_at': row['updated_at']})
        return result

    def ask(self, uid, request_id, question, *, source_record=None, recent_records=()):
        source_id = str(source_record['_id']) if source_record else None
        selected = list(recent_records)
        # Context helps resolve references; it never creates synthetic Memory IDs.
        def context_question():
            return json.dumps({'current_question': question, 'recent_user_messages': [
                {'content': item['content'], 'created_at': item['created_at'].isoformat()} for item in reversed(selected)]}, ensure_ascii=False)
        while selected and len(context_question()) > 10000:
            selected.pop()
        prompt_question = context_question() if selected else question
        inputs = ([source_record] if source_record else []) + selected
        record_inputs = [{'record_id': str(item['_id']), 'fingerprint': record_fingerprint(item)} for item in inputs]
        row, created = self.repository.start(uid, request_id, question,
            **({'source_record_id': source_id, 'record_inputs': record_inputs} if source_id else {}))
        if not created:
            return self.public(uid, [row])[0]
        try:
            previous = [old for old in self.repository.page(uid, limit=3, cursor=(row['created_at'], row['_id']))
                        if old['_id'] != row['_id'] and old['state'] == 'succeeded' and self.records_valid(uid, old)][:3]
            inherited = {ref['record_id']: ref for ref in row.get('record_inputs', [])}
            bounded_previous = []
            for old in previous:
                combined = {**inherited, **{ref['record_id']: ref for ref in old.get('record_inputs', [])}}
                if len(combined) <= 20:
                    bounded_previous.append(old)
                    inherited = combined
            previous = bounded_previous
            if inherited:
                row['record_inputs'] = list(inherited.values())
            if not self.records_valid(uid, row):
                raise AnswerError('sources_changed')
            generated = self.answerer.generate(uid, prompt_question, previous)
            if not self.records_valid(uid, row):
                raise AnswerError('sources_changed')
            processing = {'provider': 'gemini', 'model': self.answerer.extractor.model,
                          'version': ANSWER_VERSION, 'prompt_version': ANSWER_PROMPT_VERSION}
            if source_id:
                processing['context_version'] = 'unified-record-context-v1'
            retrieval = generated.get('retrieval', {'mode': 'direct'})
            if isinstance(retrieval, dict):
                processing['retrieval'] = retrieval
            finished = self.repository.finish(row, 'succeeded', answer=generated['answer'].model_dump(),
                inputs=generated['inputs'], processing=processing,
                **({'record_inputs': row['record_inputs']} if inherited else {}))
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
