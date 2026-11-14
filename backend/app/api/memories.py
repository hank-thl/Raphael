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
from app.repositories.content_types import normalize_name
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


@router.get('/topics')
def topic_catalog(user=Depends(require_admin), reader=Depends(get_memory_reader),
                  expected_uid: Annotated[str | None, Header(alias='X-Raphael-Account', max_length=128)] = None):
    if expected_uid is not None and expected_uid != user.uid:
        raise HTTPException(409, '帳號已變更，請重新開啟記憶頁面')
    counts, unclassified, total = {}, 0, 0
    try:
        for row in reader.memories.recent(user.uid):
            memory = reader.get_eligible(user.uid, row['_id'])
            if memory is None or memory.get('lifecycle') != 'candidate':
                continue
            names = set(memory['versions'][-1]['content'].get('topics', []))
            total += 1
            if not names:
                unclassified += 1
            for name in names:
                counts[name] = counts.get(name, 0) + 1
    except PyMongoError:
        raise HTTPException(503, '記憶服務暫時無法使用') from None
    return {'topics': [{'name': name, 'count': counts[name]} for name in sorted(counts)],
            'unclassified_count': unclassified, 'total_count': total}


@router.get('')
def list_memories(user=Depends(require_admin), reader=Depends(get_memory_reader),
                  limit: Annotated[int, Query(ge=1, le=20)] = 10,
                  cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
                  topic: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
                  unclassified: bool = False,
                  expected_uid: Annotated[str | None, Header(alias='X-Raphael-Account', max_length=128)] = None):
    if expected_uid is not None and expected_uid != user.uid:
        raise HTTPException(409, '帳號已變更，請重新開啟記憶頁面')
    query = {'user_uid': user.uid, 'lifecycle': 'candidate'}
    if topic is not None:
        if unclassified:
            raise HTTPException(422, '不可同時指定主題與尚未分類')
        try:
            topic = normalize_name(topic)
        except ValueError:
            raise HTTPException(422, '無效的主題') from None
        # Match any revision in MongoDB, then verify the current version below.
        query['versions.content.topics'] = topic
    if cursor is not None:
        timestamp, identifier = decode_boundary(cursor)
        query['$or'] = [{'updated_at': {'$lt': timestamp}}, {'updated_at': timestamp, '_id': {'$lt': identifier}}]
    try:
        scanned = list(reader.memories.collection.find(query).sort([('updated_at', -1), ('_id', -1)]).limit(limit + 1))
        items = []
        for row in scanned[:limit]:
            eligible = reader.get_eligible(user.uid, row['_id'])
            if eligible is not None and eligible.get('lifecycle') == 'candidate':
                names = eligible['versions'][-1]['content'].get('topics', [])
                if (topic is not None and topic not in names) or (unclassified and names):
                    continue
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
