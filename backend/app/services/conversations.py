"""Save dialogue independently; never feed it to the memory capture worker."""
from itertools import islice

from app.repositories.records import utc_now
from app.schemas.memory_answer import MemoryAnswer
from app.services.gemini import GeminiError
from app.services.memory_answer import ANSWER_VERSION, AnswerError, MemoryAnswerService


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
            result.append({'id': row['_id'], 'request_id': row['request_id'], 'question': row['question'],
                           'state': state, 'answer': answer, 'error': error,
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
            finished = self.repository.finish(row, 'succeeded', answer=generated['answer'].model_dump(),
                inputs=generated['inputs'], processing={'provider': 'gemini', 'model': self.answerer.extractor.model,
                                                        'version': ANSWER_VERSION})
        except (GeminiError, AnswerError) as exc:
            reason = str(exc) if isinstance(exc, AnswerError) else exc.reason
            safe = reason if reason in {'input_limit', 'sources_changed', 'rate_limited'} else 'generation_failed'
            finished = self.repository.finish(row, 'failed', error=safe)
        if finished is None:
            finished = self.repository.get(uid, request_id)
        return self.public(uid, [finished])[0]
