"""Owner dialogue history and grounded answers, independent of capture records."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field, field_validator
from pymongo.errors import PyMongoError

from app.api.admin import require_admin
from app.api.memories import get_memory_reader
from app.core.config import get_settings
from app.core.security import require_csrf
from app.db import get_database
from app.repositories.conversations import ConversationsRepository, decode_cursor, encode_cursor
from app.schemas.extraction import StrictModel
from app.services.conversations import ConversationService
from app.services.gemini import GeminiExtractor
from app.services.memory_answer import MemoryAnswerService

router = APIRouter(prefix='/conversations', tags=['conversations'])


class Question(StrictModel):
    content: str = Field(min_length=1, max_length=10000)
    request_id: str = Field(min_length=36, max_length=36)

    @field_validator('content')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Empty question')
        return value

    @field_validator('request_id')
    @classmethod
    def uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('Use canonical UUID')
        return value


def get_conversation_service(request: Request, reader=Depends(get_memory_reader), settings=Depends(get_settings)):
    return ConversationService(ConversationsRepository(get_database(request)['conversations']),
        MemoryAnswerService(reader, GeminiExtractor(settings.gemini_api_key, settings.gemini_model)))


def account(user, expected):
    if expected is not None and expected != user.uid:
        raise HTTPException(409, detail={'code': 'account_changed'})


@router.get('')
def history(user=Depends(require_admin), service=Depends(get_conversation_service),
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    expected: Annotated[str | None, Header(alias='X-Raphael-Account', max_length=128)] = None):
    account(user, expected)
    try:
        boundary = decode_cursor(cursor) if cursor is not None else None
    except ValueError:
        raise HTTPException(400, detail={'code': 'invalid_cursor'}) from None
    try:
        rows = service.repository.page(user.uid, limit, boundary)
        return {'items': service.public(user.uid, rows[:limit]),
                'next_cursor': encode_cursor(rows[limit - 1]) if len(rows) > limit else None}
    except PyMongoError:
        raise HTTPException(503, detail={'code': 'storage_unavailable'}) from None


@router.get('/{request_id}')
def status(request_id: UUID, user=Depends(require_admin), service=Depends(get_conversation_service),
    expected: Annotated[str | None, Header(alias='X-Raphael-Account', max_length=128)] = None):
    account(user, expected)
    try:
        row = service.repository.get(user.uid, str(request_id))
        if row is None:
            raise HTTPException(404, detail={'code': 'not_found'})
        return service.public(user.uid, [row])[0]
    except PyMongoError:
        raise HTTPException(503, detail={'code': 'storage_unavailable'}) from None


@router.post('', dependencies=[Depends(require_csrf)])
def ask(body: Question, user=Depends(require_admin), service=Depends(get_conversation_service),
    expected: Annotated[str | None, Header(alias='X-Raphael-Account', max_length=128)] = None):
    account(user, expected)
    try:
        return service.ask(user.uid, body.request_id, body.content)
    except ValueError:
        raise HTTPException(409, detail={'code': 'request_conflict'}) from None
    except PyMongoError:
        raise HTTPException(503, detail={'code': 'storage_unavailable'}) from None
