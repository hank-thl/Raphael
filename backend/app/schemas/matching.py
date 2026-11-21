"""Decision contract separate from extraction; system IDs remain backend-controlled."""
from typing import Literal
from pydantic import Field, field_validator, model_validator
from app.schemas.extraction import StrictModel, MemoryCandidate


class MatchDecision(StrictModel):
    action: Literal['new', 'supplement', 'uncertain']
    memory_id: str | None
    merged: MemoryCandidate | None
    decision_note: str | None = Field(default=None, min_length=1, max_length=300)

    @field_validator('decision_note')
    @classmethod
    def nonblank_note(cls, value):
        if value is not None and not value.strip():
            raise ValueError('Blank decision note')
        return value

    @model_validator(mode='after')
    def coherent(self):
        if self.action == 'supplement':
            if not self.memory_id or self.merged is None:
                raise ValueError('Supplement requires target and merged candidate')
        elif self.memory_id is not None or self.merged is not None:
            raise ValueError('New/uncertain cannot mutate existing memories')
        return self
