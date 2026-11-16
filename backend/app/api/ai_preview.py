"""Owner-only, transient memory extraction tests. No persistence or job enqueue."""
from datetime import datetime, timezone
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import Field, field_validator

from app.core.ai_access import require_ai_user
from app.core.config import get_settings
from app.core.security import require_csrf
from app.schemas.extraction import ExtractionInput, ExtractionResult, SourceRecord, StrictModel
from app.services.gemini import CONTRACT_VERSION, PROMPT_VERSION, GeminiError, GeminiExtractor

router = APIRouter(prefix='/admin/ai-preview', tags=['admin'])
_preview_lock = Lock()


class PreviewRequest(StrictModel):
    content: str = Field(min_length=1, max_length=10000)

    @field_validator('content')
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError('Empty content')
        return value  # Never strip or translate the source text.


class PreviewResponse(StrictModel):
    provider: str
    model: str
    prompt_version: str
    contract_version: str
    analysis: ExtractionResult
    persisted: bool


def get_preview_extractor(settings=Depends(get_settings)):
    return GeminiExtractor(settings.gemini_api_key, settings.gemini_model)


@router.post('', response_model=PreviewResponse, dependencies=[Depends(require_csrf)])
def preview(body: PreviewRequest, user=Depends(require_ai_user), extractor=Depends(get_preview_extractor),
            expected_uid: Annotated[str | None, Header(alias='X-Raphael-Account', max_length=128)] = None):
    if expected_uid is not None and expected_uid != user.uid:
        raise HTTPException(409, detail={'code': 'account_changed'})
    if not _preview_lock.acquire(blocking=False):
        raise HTTPException(429, detail={'code': 'preview_busy'})
    try:
        data = ExtractionInput(target=SourceRecord(record_id='preview_1', content=body.content,
            created_at=datetime.now(timezone.utc)), timezone='Asia/Taipei')
        result = extractor.analyze(data)
        result.validate_sources(data)
        return PreviewResponse(provider='gemini', model=extractor.model,
            prompt_version=PROMPT_VERSION, contract_version=CONTRACT_VERSION,
            analysis=result, persisted=False)
    except GeminiError as exc:
        if exc.reason == 'rate_limited':
            raise HTTPException(429, detail={'code': 'rate_limited'}) from None
        raise HTTPException(503, detail={'code': 'preview_failed'}) from None
    except (ValueError, TypeError):
        raise HTTPException(502, detail={'code': 'invalid_preview'}) from None
    finally:
        _preview_lock.release()
