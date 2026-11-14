"""Owner-only job status/retry. Never accepts content, UID or old-record backfill."""
from typing import Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from pymongo.errors import PyMongoError

from app.core.config import get_settings
from app.core.security import get_current_user, require_csrf
from app.db import get_database
from app.repositories.memory_jobs import MemoryJobsRepository

router = APIRouter(prefix='/records', tags=['memory-processing'])


def require_capture_owner(user=Depends(get_current_user), settings=Depends(get_settings)):
    if not settings.ai_capture_enabled or not settings.admin_uid or user.uid != settings.admin_uid:
        raise HTTPException(403, '此帳號尚未開放記憶分析')
    return user


def get_memory_jobs(request: Request):
    return MemoryJobsRepository(get_database(request)['records'])


class MemoryJobStatus(BaseModel):
    state: Literal['not_requested', 'pending', 'running', 'succeeded', 'failed']
    attempts: int = 0
    error: str | None = None


def status_response(row):
    job = row.get('ai_processing')
    if not job:
        return MemoryJobStatus(state='not_requested')
    return MemoryJobStatus(state=job['state'], attempts=job['attempts'], error=job.get('error'))


@router.get('/{record_id}/memory', response_model=MemoryJobStatus)
def memory_status(record_id: str, user=Depends(require_capture_owner), jobs=Depends(get_memory_jobs)):
    if not ObjectId.is_valid(record_id):
        raise HTTPException(404, '找不到紀錄')
    try:
        row = jobs.get(user.uid, record_id)
    except PyMongoError:
        raise HTTPException(503, '無法讀取分析狀態') from None
    if row is None:
        raise HTTPException(404, '找不到紀錄')
    return status_response(row)


@router.post('/{record_id}/memory/retry', response_model=MemoryJobStatus,
             dependencies=[Depends(require_csrf)], status_code=202)
def retry_memory(record_id: str, user=Depends(require_capture_owner), jobs=Depends(get_memory_jobs)):
    if not ObjectId.is_valid(record_id):
        raise HTTPException(404, '找不到紀錄')
    try:
        jobs.expire(user.uid)
        existing = jobs.get(user.uid, record_id)
        if existing is None:
            raise HTTPException(404, '找不到紀錄')
        row = jobs.retry(user.uid, record_id)
    except PyMongoError:
        raise HTTPException(503, '分析重試狀態未確認，請重新查詢') from None
    if row is None:
        raise HTTPException(409, '只能重試已失敗的新訊息分析')
    return status_response(row)
