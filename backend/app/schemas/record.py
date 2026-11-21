from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RecordCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(strict=True, min_length=1, max_length=10000)

    @field_validator("content")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("紀錄不可只有空白")
        return value


class MemoryJobStatus(BaseModel):
    state: Literal['not_requested', 'pending', 'running', 'succeeded', 'failed']
    attempts: int = 0
    error: str | None = None


def memory_status_response(row):
    job = row.get('ai_processing')
    if not job:
        return MemoryJobStatus(state='not_requested')
    return MemoryJobStatus(state=job['state'], attempts=job['attempts'], error=job.get('error'))


class RecordResponse(BaseModel):
    id: str
    content: str
    created_at: datetime
    memory_processing: MemoryJobStatus | None = None


class RecordPage(BaseModel):
    items: list[RecordResponse]
    next_cursor: str | None = None
