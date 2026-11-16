"""Memory-grounded answers; citations identify exact existing memory versions."""
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.extraction import StrictModel
from app.schemas.topic_summary import SummarySource


class AnswerPassage(StrictModel):
    text: str = Field(min_length=1, max_length=2500)
    basis: Literal['explicit', 'contextual', 'inferred', 'limitation', 'general']
    sources: list[SummarySource] = Field(max_length=20)

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Blank answer')
        return value


class MemoryAnswer(StrictModel):
    passages: list[AnswerPassage] = Field(min_length=1, max_length=10)
    insufficient_information: bool

    def validate_sources(self, memories):
        allowed = {(row['_id'], version['revision']): version
                   for row in memories for version in row['versions']}
        for passage in self.passages:
            refs = [(source.memory_id, source.revision) for source in passage.sources]
            if len(set(refs)) != len(refs) or any(ref not in allowed for ref in refs):
                raise ValueError('Invalid answer sources')
            if passage.basis == 'general' and refs:
                raise ValueError('General knowledge cannot use memory citations')
            if passage.basis not in {'limitation', 'general'} and not refs:
                raise ValueError('Memory claims require citations')
            if any(allowed[ref]['content']['evidence']['type'] == 'inferred' for ref in refs) and passage.basis not in {'inferred', 'limitation'}:
                raise ValueError('Inferred evidence cannot become facts')
        if not memories and not self.insufficient_information and not all(
                passage.basis == 'general' for passage in self.passages):
            raise ValueError('Empty memory cannot support a personal answer')
