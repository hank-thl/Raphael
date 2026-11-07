"""Small owner-only access list. Firebase identity and Raphael access are distinct."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from pymongo.errors import PyMongoError

from app.core.config import Settings, get_settings
from app.core.firebase import get_auth_service
from app.core.security import CurrentUser, get_current_user, require_csrf
from app.db import get_database

router = APIRouter(prefix="/admin", tags=["admin"])


def collection(request):
    return get_database(request)["access_users"]



def require_admin(user: CurrentUser = Depends(get_current_user), settings: Settings = Depends(get_settings)):
    if not settings.admin_uid or user.uid != settings.admin_uid:
        raise HTTPException(403, "僅管理員可使用此功能")
    return user


class Account(BaseModel):
    uid: str
    email: str | None = None
    enabled: bool
    is_admin: bool = False


class AddAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        value = value.strip()
        if value.count("@") != 1 or any(c.isspace() for c in value) or not all(value.split("@")):
            raise ValueError("請輸入有效的電子郵件")
        return value


class ChangeAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


@router.get("/me")
def admin_me(_user=Depends(require_admin)):
    return {"is_admin": True}


@router.get("/users", response_model=list[Account])
def list_accounts(request: Request, user=Depends(require_admin), settings=Depends(get_settings)):
    try:
        entries = {row["_id"]: Account(uid=row["_id"], email=row.get("email"), enabled=row.get("enabled") is True)
                   for row in collection(request).find({})}
    except PyMongoError:
        raise HTTPException(503, "無法讀取允許清單") from None
    for uid in settings.allowed_uids:
        entries.setdefault(uid, Account(uid=uid, enabled=True))
    entries[user.uid] = Account(uid=user.uid, email=user.email, enabled=True, is_admin=True)
    return sorted(entries.values(), key=lambda row: (not row.is_admin, row.email or row.uid))


@router.post("/users", response_model=Account, dependencies=[Depends(require_csrf)])
def add_account(body: AddAccount, request: Request, user=Depends(require_admin),
                settings=Depends(get_settings), service=Depends(get_auth_service)):
    # Check DB availability before provisioning; two services cannot share a transaction.
    store = collection(request)
    try:
        store.find_one({"_id": user.uid})
    except PyMongoError:
        raise HTTPException(503, "帳號授權服務暫時無法使用") from None
    try:
        account = service.ensure_account(body.email)
    except Exception:
        raise HTTPException(503, "無法準備 Firebase 帳號，請確認信箱或服務設定後重試") from None
    if account.uid == settings.admin_uid:
        return Account(uid=user.uid, email=user.email, enabled=True, is_admin=True)
    now = datetime.now(timezone.utc)
    try:
        # Adding an existing account never silently re-enables it.
        store.update_one({"_id": account.uid}, {"$setOnInsert": {"enabled": True, "created_at": now},
                         "$set": {"email": account.email, "updated_at": now, "updated_by": user.uid}}, upsert=True)
        row = store.find_one({"_id": account.uid})
    except PyMongoError:
        raise HTTPException(503, "Firebase 帳號可能已準備完成，但授權儲存未確認；請重新整理清單後重試") from None
    return Account(uid=account.uid, email=row.get("email"), enabled=row.get("enabled") is True)


@router.patch("/users/{uid}", response_model=Account, dependencies=[Depends(require_csrf)])
def change_access(uid: str, body: ChangeAccess, request: Request, user=Depends(require_admin), settings=Depends(get_settings)):
    if uid == settings.admin_uid:
        raise HTTPException(403, "不能變更管理員的使用權限")
    if not 1 <= len(uid) <= 128:
        raise HTTPException(404, "找不到帳號")
    store = collection(request)
    try:
        row = store.find_one({"_id": uid})
        if row is None and uid not in settings.allowed_uids:
            raise HTTPException(404, "找不到帳號")
        store.update_one({"_id": uid}, {"$set": {"enabled": body.enabled, "updated_at": datetime.now(timezone.utc),
                         "updated_by": user.uid}}, upsert=uid in settings.allowed_uids)
    except PyMongoError:
        raise HTTPException(503, "權限更新未確認，請重新整理清單後確認") from None
    return Account(uid=uid, email=row.get("email") if row else None, enabled=body.enabled)
