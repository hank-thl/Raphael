"""Article-oriented synthesis v2. Citations remain tied to exact memory revisions."""
import unicodedata
from typing import Literal
from pydantic import Field, field_validator
from app.schemas.extraction import StrictModel
from app.schemas.topic_summary import SummarySource


class SynthesisPassage(StrictModel):
    text: str = Field(min_length=1, max_length=2500)
    basis: Literal['explicit', 'contextual', 'inferred']
    sources: list[SummarySource] = Field(min_length=1, max_length=50)
    time_label: str | None = Field(max_length=120)

    @field_validator('text', 'time_label')
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip(): raise ValueError('Blank passage')
        return value


class SynthesisSection(StrictModel):
    heading: str = Field(min_length=1, max_length=120)
    layout: Literal['paragraphs', 'bullets', 'timeline']
    items: list[SynthesisPassage] = Field(min_length=1, max_length=12)

    @field_validator('heading')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('Blank heading')
        return value


class MemorySynthesisResult(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    summary: list[SynthesisPassage] = Field(min_length=1, max_length=3)
    sections: list[SynthesisSection] = Field(max_length=6)

    @field_validator('title')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('Blank title')
        return value

    def validate_sources(self, memories):
        allowed = {}
        for memory in memories:
            for version in memory['versions']:
                allowed[(memory['_id'], version['revision'])] = version
        passages = [*self.summary, *(item for section in self.sections for item in section.items)]
        seen = set()
        for passage in passages:
            text = ''.join(unicodedata.normalize('NFKC', passage.text).casefold().split())
            if text in seen: raise ValueError('Repeated passage')
            seen.add(text)
            refs = [(source.memory_id, source.revision) for source in passage.sources]
            if len(set(refs)) != len(refs) or any(ref not in allowed for ref in refs):
                raise ValueError('Invalid synthesis citations')
            if any(allowed[ref]['content']['evidence']['type'] == 'inferred' for ref in refs) and passage.basis != 'inferred':
                raise ValueError('Inferred evidence cannot become facts')
        for section in self.sections:
            if section.layout == 'timeline' and any(not item.time_label for item in section.items):
                raise ValueError('Timeline items require supported time labels')
