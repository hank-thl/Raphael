"""Read-only owner preview. Revalidate all sources before exposing any text."""
import base64
import binascii
import json
import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pymongo.errors import PyMongoError

from app.api.admin import require_admin
from app.db import get_database
from app.repositories.analyses import AnalysesRepository
from app.repositories.memories import MemoriesRepository
from app.services.memory_capture import MemoryCaptureService
from app.services.memory_versions import MemoryVersionsService

router = APIRouter(prefix='/memories', tags=['memories'])


def get_memory_reader(request: Request):
    db = get_database(request)
    # No extractor, key or model call is needed to read already saved memories.
    return MemoryVersionsService(MemoryCaptureService(db['records'], AnalysesRepository(db['analyses']), None, ''),
                                 MemoriesRepository(db['memories']))


def encode_boundary(row):
    return base64.urlsafe_b64encode(json.dumps([row['updated_at'].isoformat(), row['_id']]).encode()).decode()


def decode_boundary(cursor):
    try:
        value = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(x, str) for x in value):
            raise ValueError()
        timestamp = datetime.fromisoformat(value[0])
        if timestamp.tzinfo is None or not re.fullmatch(r'[a-f0-9]{64}', value[1]):
            raise ValueError()
        return timestamp, value[1]
    except (ValueError, TypeError, binascii.Error, OverflowError):
        raise HTTPException(400, '無效的分頁游標') from None


def summary(row):
    content = row['versions'][-1]['content']
    return {'id': row['_id'], 'revision': row['revision'], 'updated_at': row['updated_at'],
            'text': content['text'], 'kind': content['kind'], 'status': content['status'],
            'topics': content.get('topics', []),
            'evidence_type': content['evidence']['type'], 'time_text': content['time_text']}


@router.get('')
def list_memories(user=Depends(require_admin), reader=Depends(get_memory_reader),
                  limit: Annotated[int, Query(ge=1, le=20)] = 10,
                  cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
                  expected_uid: Annotated[str | None, Header(alias='X-Raphael-Account', max_length=128)] = None):
    if expected_uid is not None and expected_uid != user.uid:
        raise HTTPException(409, '帳號已變更，請重新開啟記憶頁面')
    query = {'user_uid': user.uid, 'lifecycle': 'candidate'}
    if cursor is not None:
        timestamp, identifier = decode_boundary(cursor)
        query['$or'] = [{'updated_at': {'$lt': timestamp}}, {'updated_at': timestamp, '_id': {'$lt': identifier}}]
    try:
        scanned = list(reader.memories.collection.find(query).sort([('updated_at', -1), ('_id', -1)]).limit(limit + 1))
        items = []
        for row in scanned[:limit]:
            eligible = reader.get_eligible(user.uid, row['_id'])
            if eligible is not None:
                items.append(summary(eligible))
        # Cursor follows scanned rows, even when all their sources are invalid.
        return {'items': items, 'next_cursor': encode_boundary(scanned[limit - 1]) if len(scanned) > limit else None}
    except PyMongoError:
        raise HTTPException(503, '記憶服務暫時無法使用') from None


@router.get('/{memory_id}')
def memory_detail(memory_id: str, user=Depends(require_admin), reader=Depends(get_memory_reader)):
    if not re.fullmatch(r'[a-f0-9]{64}', memory_id):
        raise HTTPException(404, '找不到記憶')
    try:
        row = reader.get_eligible(user.uid, memory_id)
    except PyMongoError:
        raise HTTPException(503, '記憶服務暫時無法使用') from None
    if row is None or row.get('lifecycle') != 'candidate':
        raise HTTPException(404, '找不到記憶')
    return {**summary(row), 'versions': [
        {'revision': version['revision'], 'created_at': version['created_at'], 'topics': [], **version['content']}
        for version in row['versions']]}
