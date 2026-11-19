"""Candidate relevance is distinct from whether evidence answers the question."""
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.extraction import StrictModel


class FilterCandidate(StrictModel):
    memory_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    revision: int = Field(strict=True, ge=1)
    text: str = Field(min_length=1, max_length=4000)
    kind: list[str] = Field(max_length=16)
    topics: list[str] = Field(max_length=8)
    status: str | None
    evidence_type: Literal['explicit', 'contextual', 'inferred']
    time_text: str | None


class CandidateDecision(StrictModel):
    memory_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    relevance: Literal['direct', 'background', 'irrelevant']
    quote: str | None = Field(max_length=4000)
    reason: str = Field(min_length=1, max_length=200)

    @field_validator('reason')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Blank reason')
        return value


class CandidateFilterResult(StrictModel):
    items: list[CandidateDecision] = Field(min_length=1, max_length=10)
    answerability: Literal['sufficient', 'partial', 'insufficient']
    explanation: str = Field(min_length=1, max_length=500)

    @field_validator('explanation')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Blank explanation')
        return value

    @property
    def keep_ids(self):
        return [item.memory_id for item in self.items if item.relevance != 'irrelevant']

    def validate_candidates(self, candidates):
        allowed = {item.memory_id: item for item in candidates}
        ids = [item.memory_id for item in self.items]
        if len(set(ids)) != len(ids) or set(ids) != set(allowed):
            raise ValueError('All candidate IDs must occur exactly once')
        kept = []
        for item in self.items:
            if item.relevance == 'irrelevant':
                if item.quote is not None:
                    raise ValueError('Irrelevant candidates must have null quote')
            else:
                if not item.quote or not item.quote.strip() or item.quote not in allowed[item.memory_id].text:
                    raise ValueError('Relevant candidates require an exact text excerpt')
                kept.append(item)
        if self.answerability != 'insufficient' and not kept:
            raise ValueError('No relevant evidence cannot support answerability')
        if self.answerability == 'sufficient' and not any(item.relevance == 'direct' for item in kept):
            raise ValueError('Background alone cannot fully answer')


class CompactFilterResult(StrictModel):
    keep_ids: list[str] = Field(max_length=10)
    answerability: Literal['sufficient', 'partial', 'insufficient']
    limitation: str | None = Field(max_length=180)

    def validate_candidates(self, candidates):
        self.validate_ids([item.memory_id for item in candidates])

    def validate_ids(self, candidate_ids):
        allowed = set(candidate_ids)
        if len(set(self.keep_ids)) != len(self.keep_ids) or not set(self.keep_ids) <= allowed:
            raise ValueError('Kept IDs must be unique supplied candidate IDs')
        if self.answerability != 'insufficient' and not self.keep_ids:
            raise ValueError('No evidence cannot support an answer')
        if self.answerability == 'sufficient':
            if self.limitation is not None:
                raise ValueError('Sufficient needs no limitation')
        elif not self.limitation or not self.limitation.strip():
            raise ValueError('Limited evidence requires a brief concrete limitation')
