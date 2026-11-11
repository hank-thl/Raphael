import logging
from contextlib import contextmanager
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pymongo.errors import PyMongoError

from app.core.security import CurrentUser, get_current_user, require_csrf
from app.db import get_database
from app.repositories.records import RecordsRepository, decode_cursor
from app.schemas.record import RecordCreate, RecordPage, RecordResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/records", tags=["records"], dependencies=[Depends(get_current_user)])


def get_records_repository(request: Request) -> RecordsRepository:
    return RecordsRepository(get_database(request)["records"])


@contextmanager
def database_operation():
    try:
        yield
    except PyMongoError as exc:
        logger.warning("Record operation failed (%s)", type(exc).__name__)
        raise HTTPException(503, "紀錄服務暫時無法使用，請稍後重試") from None


@router.post("", response_model=RecordResponse, status_code=201, dependencies=[Depends(require_csrf)])
def create_record(
    body: RecordCreate,
    user: CurrentUser = Depends(get_current_user),
    repository: RecordsRepository = Depends(get_records_repository),
) -> RecordResponse:
    with database_operation():
        return repository.create(user.uid, body.content)


@router.get("", response_model=RecordPage)
def list_records(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    expected_uid: Annotated[str | None, Header(alias="X-Raphael-Account", max_length=128)] = None,
    user: CurrentUser = Depends(get_current_user),
    repository: RecordsRepository = Depends(get_records_repository),
) -> RecordPage:
    # This header only checks page consistency; identity always comes from the session.
    if expected_uid is not None and expected_uid != user.uid:
        raise HTTPException(409, "帳號已變更，請重新開啟聊天室")
    try:
        boundary = decode_cursor(cursor) if cursor is not None else None
    except ValueError:
        raise HTTPException(400, "無效的分頁游標") from None
    with database_operation():
        return repository.list(user.uid, limit, boundary)


@router.delete("/{record_id}", status_code=204, dependencies=[Depends(require_csrf)])
def delete_record(
    record_id: str,
    user: CurrentUser = Depends(get_current_user),
    repository: RecordsRepository = Depends(get_records_repository),
) -> Response:
    if not ObjectId.is_valid(record_id):
        raise HTTPException(404, "找不到紀錄")
    with database_operation():
        deleted = repository.soft_delete(user.uid, ObjectId(record_id))
    if not deleted:
        raise HTTPException(404, "找不到紀錄")
    return Response(status_code=204)
