"""Dynamic access overrides with an explicit owner and legacy UID fallback."""
import logging
from fastapi import HTTPException
from pymongo.errors import PyMongoError
from app.db import get_database

logger = logging.getLogger(__name__)

def account_access_allowed(request, uid, settings):
    if uid == settings.admin_uid:
        return True
    try:
        entry = get_database(request)["access_users"].find_one({"_id": uid})
    except HTTPException:
        logger.warning("Authentication unavailable stage=access_database")
        raise
    except PyMongoError as exc:
        logger.warning("Authentication unavailable stage=access_lookup exception=%s", type(exc).__name__)
        raise HTTPException(503, "帳號授權服務暫時無法使用") from None
    # An explicit disabled document overrides the legacy environment allowlist.
    return entry.get("enabled") is True if entry is not None else uid in settings.allowed_uids

