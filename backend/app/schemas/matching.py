"""Decision contract separate from extraction; system IDs remain backend-controlled."""
from typing import Literal
from pydantic import model_validator
from app.schemas.extraction import StrictModel, MemoryCandidate


class MatchDecision(StrictModel):
    action: Literal['new', 'supplement', 'uncertain']
    memory_id: str | None
    merged: MemoryCandidate | None

    @model_validator(mode='after')
    def coherent(self):
        if self.action == 'supplement':
            if not self.memory_id or self.merged is None:
                raise ValueError('Supplement requires target and merged candidate')
        elif self.memory_id is not None or self.merged is not None:
            raise ValueError('New/uncertain cannot mutate existing memories')
        return self
