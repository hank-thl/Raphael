"""Experimental combined selection/answer contract; existing answer contract is nested unchanged."""
from app.schemas.candidate_filter import CompactFilterResult
from app.schemas.memory_answer import MemoryAnswer


class JointMemoryAnswer(CompactFilterResult):
    answer: MemoryAnswer

    def validate_memories(self, memories):
        self.validate_ids([row['_id'] for row in memories])
        selected = [row for row in memories if row['_id'] in self.keep_ids]
        self.answer.validate_sources(selected)
        if self.answer.insufficient_information != (self.answerability != 'sufficient'):
            raise ValueError('Answerability and answer flag must agree')
