"""Dynamic access overrides with an explicit owner and legacy UID fallback."""
from fastapi import HTTPException
from pymongo.errors import PyMongoError
from app.db import get_database

def account_access_allowed(request, uid, settings):
    if uid == settings.admin_uid:
        return True
    try:
        entry = get_database(request)["access_users"].find_one({"_id": uid})
    except PyMongoError:
        raise HTTPException(503, "帳號授權服務暫時無法使用") from None
    # An explicit disabled document overrides the legacy environment allowlist.
    return entry.get("enabled") is True if entry is not None else uid in settings.allowed_uids

