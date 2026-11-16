"""Manual, owner-only topic summary API; no generation on GET."""
from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field
from pymongo.errors import PyMongoError
from app.core.ai_access import require_ai_user
from app.api.memories import get_memory_reader
from app.core.config import get_settings
from app.core.security import require_csrf
from app.db import get_database
from app.repositories.content_types import normalize_name
from app.repositories.topic_summaries import TopicSummariesRepository
from app.schemas.extraction import StrictModel
from app.services.gemini import GeminiError, GeminiExtractor
from app.services.topic_summary import SummaryError, TopicSummaryService

router = APIRouter(prefix='/topic-summary', tags=['topic-summary'])


class GenerateSummary(StrictModel):
    topic: str = Field(min_length=1, max_length=64)


def get_summary_service(request: Request, reader=Depends(get_memory_reader), settings=Depends(get_settings)):
    return TopicSummaryService(reader, TopicSummariesRepository(get_database(request)['analyses']),
                               GeminiExtractor(settings.gemini_api_key, settings.gemini_model))


def normalized_topic(topic, uid, expected_uid):
    if expected_uid is not None and expected_uid != uid:
        raise HTTPException(409, detail={'code': 'account_changed'})
    try: return normalize_name(topic)
    except ValueError: raise HTTPException(422, detail={'code': 'invalid_topic'}) from None


@router.get('')
def read_summary(topic: Annotated[str, Query(min_length=1, max_length=64)], user=Depends(require_ai_user),
    service=Depends(get_summary_service), expected_uid: Annotated[str | None, Header(alias='X-Raphael-Account', max_length=128)] = None):
    topic = normalized_topic(topic, user.uid, expected_uid)
    try: return service.read(user.uid, topic)
    except PyMongoError: raise HTTPException(503, detail={'code': 'storage_unavailable'}) from None


@router.post('', dependencies=[Depends(require_csrf)])
def generate_summary(body: GenerateSummary, user=Depends(require_ai_user), service=Depends(get_summary_service),
    expected_uid: Annotated[str | None, Header(alias='X-Raphael-Account', max_length=128)] = None):
    topic = normalized_topic(body.topic, user.uid, expected_uid)
    try: return service.generate(user.uid, topic)
    except SummaryError as exc:
        code = str(exc)
        raise HTTPException(413 if code == 'input_limit' else 502 if code == 'invalid_summary' else 409,
                            detail={'code': code}) from None
    except GeminiError: raise HTTPException(503, detail={'code': 'generation_failed'}) from None
    except PyMongoError: raise HTTPException(503, detail={'code': 'storage_unavailable'}) from None
