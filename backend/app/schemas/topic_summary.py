"""LLM summary output; ownership and system fields remain backend-controlled."""
from typing import Literal
from pydantic import Field, field_validator
from app.schemas.extraction import StrictModel


class SummarySource(StrictModel):
    memory_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    revision: int = Field(ge=1)


class SummaryItem(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    basis: Literal['explicit', 'contextual', 'inferred']
    sources: list[SummarySource] = Field(min_length=1, max_length=50)

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('Blank summary text')
        return value


class TopicSummaryResult(StrictModel):
    items: list[SummaryItem] = Field(min_length=1, max_length=30)

    def validate_sources(self, memories):
        allowed = {(row['_id'], row['revision']): row for row in memories}
        for item in self.items:
            refs = [(source.memory_id, source.revision) for source in item.sources]
            if len(set(refs)) != len(refs) or any(ref not in allowed for ref in refs):
                raise ValueError('Invalid summary citations')
            if any(allowed[ref]['versions'][-1]['content']['evidence']['type'] == 'inferred' for ref in refs) and item.basis != 'inferred':
                raise ValueError('Inferred evidence cannot become facts')
