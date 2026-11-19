"""Answer a fixed, fingerprint-bound retrieval subset using the existing answer contract."""
from app.services.memory_answer import AnswerError, MemoryAnswerService


class RetrievedMemoryAnswerService(MemoryAnswerService):
    def __init__(self, reader, extractor, owner, expected):
        super().__init__(reader, extractor)
        self.owner, self.expected = owner, expected

    def collect(self, uid):
        if not uid or uid != self.owner:
            raise AnswerError('owner_required')
        if not self.expected:
            return []
        ids = [row['memory_id'] for row in self.expected]
        rows = list(self.reader.memories.collection.find(
            {'user_uid': uid, 'lifecycle': 'candidate', '_id': {'$in': ids}}))
        valid = self.reader.get_eligible_many(uid, rows)
        current = {row['_id']: row for row in valid if row.get('user_uid') == uid}
        if set(current) != set(ids) or len(valid) != len(current):
            raise AnswerError('sources_changed')
        for item in self.expected:
            if self.dependency(current[item['memory_id']])['fingerprint'] != item['fingerprint']:
                raise AnswerError('sources_changed')
        return [current[mid] for mid in ids]
