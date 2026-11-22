"""Shared timeline; mode controls replies, not personal message persistence."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pymongo.errors import PyMongoError

from app.api.conversations import Question, account
from app.core.ai_access import ai_user_allowed
from app.core.config import get_settings
from app.core.security import get_current_user, require_csrf
from app.db import get_database
from app.repositories.chat import ChatRepository, decode_cursor
from app.repositories.conversations import ConversationsRepository
from app.services.chat import ChatService
from app.services.chat_answer import build_chat_answerer
from app.services.conversations import ConversationService

router = APIRouter(prefix='/chat', tags=['chat'])


class ChatMessage(Question):
    mode: Literal['record', 'dialogue']


def get_chat_service(request: Request, settings=Depends(get_settings)):
    # Record-only users may use this endpoint without AI privileges or model calls.
    db = get_database(request)
    from app.repositories.analyses import AnalysesRepository
    from app.repositories.memories import MemoriesRepository
    from app.services.memory_capture import MemoryCaptureService
    from app.services.memory_versions import MemoryVersionsService
    reader = MemoryVersionsService(MemoryCaptureService(db['records'], AnalysesRepository(db['analyses']), None, ''),
                                   MemoriesRepository(db['memories']))
    answerer = build_chat_answerer(reader, db, settings)
    return ChatService(ChatRepository(db['records'], db['conversations']),
                       ConversationService(ConversationsRepository(db['conversations']), answerer))


def flags(uid, settings):
    allowed = ai_user_allowed(uid, settings)
    return {'capture': bool(allowed and settings.ai_capture_enabled), 'ai': allowed}


@router.get('')
def history(user=Depends(get_current_user), settings=Depends(get_settings), service=Depends(get_chat_service),
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    expected: Annotated[str | None, Header(alias='X-Raphael-Account', max_length=128)] = None):
    account(user, expected)
    try:
        boundary = decode_cursor(cursor) if cursor else None
        return service.page(user.uid, limit, boundary, **flags(user.uid, settings))
    except ValueError:
        raise HTTPException(400, detail={'code': 'invalid_cursor'}) from None
    except PyMongoError:
        raise HTTPException(503, detail={'code': 'storage_unavailable'}) from None


@router.get('/{request_id}')
def status(request_id: UUID, user=Depends(get_current_user), settings=Depends(get_settings),
    service=Depends(get_chat_service),
    expected: Annotated[str | None, Header(alias='X-Raphael-Account', max_length=128)] = None):
    account(user, expected)
    try:
        result = service.get(user.uid, str(request_id), **flags(user.uid, settings))
        if result is None:
            raise HTTPException(404, detail={'code': 'not_found'})
        return result
    except PyMongoError:
        raise HTTPException(503, detail={'code': 'storage_unavailable'}) from None


@router.post('', dependencies=[Depends(require_csrf)])
def send(body: ChatMessage, user=Depends(get_current_user), settings=Depends(get_settings),
    service=Depends(get_chat_service),
    expected: Annotated[str | None, Header(alias='X-Raphael-Account', max_length=128)] = None):
    account(user, expected)
    permissions = flags(user.uid, settings)
    if body.mode == 'dialogue' and not permissions['ai']:
        raise HTTPException(403, detail={'code': 'ai_unavailable'})
    try:
        return service.send(user.uid, body.request_id, body.content, body.mode, **permissions)
    except ValueError:
        raise HTTPException(409, detail={'code': 'request_conflict'}) from None
    except PyMongoError:
        raise HTTPException(503, detail={'code': 'storage_unavailable'}) from None
