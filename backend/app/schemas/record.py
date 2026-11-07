from datetime import datetime

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


class RecordResponse(BaseModel):
    id: str
    content: str
    created_at: datetime


class RecordPage(BaseModel):
    items: list[RecordResponse]
    next_cursor: str | None = None
