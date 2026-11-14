import base64
import binascii
import json
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.schemas.record import RecordPage, RecordResponse

RECORD_INDEX = [("user_uid", 1), ("deleted_at", 1), ("created_at", -1), ("_id", -1)]
RECORD_INDEX_NAME = "user_active_newest"


def utc_now() -> datetime:
    now = datetime.now(timezone.utc)
    # BSON dates have millisecond precision; use the same precision in responses.
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def record_response(document: dict[str, Any]) -> RecordResponse:
    return RecordResponse(id=str(document["_id"]), content=document["content"],
                          created_at=document["created_at"])


def encode_cursor(document: dict[str, Any]) -> str:
    payload = json.dumps([document["created_at"].isoformat(), str(document["_id"])])
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(value: str) -> tuple[datetime, ObjectId]:
    try:
        if not value or len(value) > 512:
            raise ValueError()
        raw = base64.b64decode(value, altchars=b"-_", validate=True)
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) != 2 or not all(isinstance(v, str) for v in payload):
            raise ValueError()
        created_at = datetime.fromisoformat(payload[0])
        if created_at.tzinfo is None:
            raise ValueError()
        return created_at.astimezone(timezone.utc), ObjectId(payload[1])
    except (ValueError, TypeError, OverflowError, binascii.Error, InvalidId) as exc:
        raise ValueError("無效的分頁游標") from exc


class RecordsRepository:
    def __init__(self, collection: Any):
        self.collection = collection

    def create(self, uid: str, content: str, *, analyze: bool = False) -> RecordResponse:
        document = {"_id": ObjectId(), "user_uid": uid, "content": content,
                    "created_at": utc_now(), "deleted_at": None}
        if analyze:
            from app.repositories.memory_jobs import pending_job
            document['ai_processing'] = pending_job()
        self.collection.insert_one(document)
        return record_response(document)

    def list(self, uid: str, limit: int, cursor: tuple[datetime, ObjectId] | None) -> RecordPage:
        query: dict[str, Any] = {"user_uid": uid, "deleted_at": None}
        if cursor is not None:
            created_at, record_id = cursor
            query["$or"] = [
                {"created_at": {"$lt": created_at}},
                {"created_at": created_at, "_id": {"$lt": record_id}},
            ]
        rows = list(self.collection.find(query).sort([("created_at", -1), ("_id", -1)]).limit(limit + 1))
        documents = rows[:limit]
        return RecordPage(
            items=[record_response(row) for row in documents],
            next_cursor=encode_cursor(documents[-1]) if len(rows) > limit else None,
        )

    def soft_delete(self, uid: str, record_id: ObjectId) -> bool:
        # Ownership and active state are checked atomically with the update.
        result = self.collection.update_one(
            {"_id": record_id, "user_uid": uid, "deleted_at": None},
            {"$set": {"deleted_at": utc_now()}},
        )
        return result.matched_count == 1

    def ensure_indexes(self) -> str:
        return self.collection.create_index(RECORD_INDEX, name=RECORD_INDEX_NAME)
