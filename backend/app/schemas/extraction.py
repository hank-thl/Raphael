"""Experimental LLM transport contract, not the final MongoDB memory schema."""

from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceRecord(StrictModel):
    record_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    content: str = Field(min_length=1, max_length=10000)
    created_at: AwareDatetime | None = None


class ExtractionInput(StrictModel):
    target: SourceRecord
    context: list[SourceRecord] = Field(default_factory=list, max_length=5)
    timezone: str | None = None
    content_types: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("content_types")
    @classmethod
    def valid_content_types(cls, values: list[str]) -> list[str]:
        from app.repositories.content_types import normalize_name
        names = [normalize_name(value) for value in values]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate content type names")
        return names

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("Unknown timezone") from None
        return value

    @model_validator(mode="after")
    def bounded_distinct_sources(self):
        rows = [self.target, *self.context]
        if len({row.record_id for row in rows}) != len(rows):
            raise ValueError("Source IDs must be distinct")
        if sum(len(row.content) for row in rows) > 20000:
            raise ValueError("Combined input exceeds 20000 characters")
        return self


class EvidenceSource(StrictModel):
    record_id: str = Field(min_length=1, max_length=64)
    quote: str = Field(min_length=1, max_length=10000)


class Evidence(StrictModel):
    type: Literal["explicit", "contextual", "inferred"]
    sources: list[EvidenceSource] = Field(min_length=1, max_length=6)


class MemoryCandidate(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    # Labels remain extensible; a memory can describe both an event and a feeling.
    kind: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(min_length=1)
    status: Literal["considering", "confirmed", "occurred", "cancelled"] | None
    evidence: Evidence
    time_text: str | None = Field(max_length=500)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Blank content is not allowed")
        return value

    @field_validator("kind")
    @classmethod
    def distinct_nonblank_kinds(cls, values: list[str]) -> list[str]:
        keys = [value.strip().casefold() for value in values]
        if any(not key for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("Kinds must be nonblank and distinct")
        return values


class ExtractionResult(StrictModel):
    content_type: str = Field(min_length=1, max_length=64)
    memory_action: Literal["extract", "context_only", "skip", "needs_context"]
    context_required: bool
    context_record_ids: list[str] = Field(max_length=5)
    items: list[MemoryCandidate] = Field(max_length=30)

    def validate_sources(self, data: ExtractionInput) -> None:
        sources = {row.record_id: row.content for row in [data.target, *data.context]}
        context_ids = {row.record_id for row in data.context}
        if len(set(self.context_record_ids)) != len(self.context_record_ids):
            raise ValueError("Duplicate context references")
        if not set(self.context_record_ids) <= context_ids:
            raise ValueError("Context references must come from the supplied context")
        if self.memory_action == "extract" and not self.items:
            raise ValueError("Extract requires candidates")
        if self.memory_action in {"skip", "context_only"} and self.items:
            raise ValueError("Skipped/context-only analysis cannot contain candidates")
        for item in self.items:
            if item.evidence.type == "inferred" and item.status is not None:
                raise ValueError("Inferred candidates cannot be assigned confirmed event states")
            ids = [source.record_id for source in item.evidence.sources]
            if data.target.record_id not in ids:
                raise ValueError("Candidates must be supported by the current target")
            references = [(source.record_id, source.quote) for source in item.evidence.sources]
            if len(set(references)) != len(references):
                raise ValueError("Duplicate evidence references")
            for source in item.evidence.sources:
                if source.record_id not in sources or source.quote not in sources[source.record_id]:
                    raise ValueError("Evidence must quote an allowed input verbatim")
                if source.record_id != data.target.record_id and source.record_id not in self.context_record_ids:
                    raise ValueError("Evidence context must be declared")
