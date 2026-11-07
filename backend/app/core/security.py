import secrets
from typing import Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.firebase import AuthService, InvalidCredential, get_auth_service

SESSION_COOKIE = "raphael_session"
CSRF_COOKIE = "raphael_csrf"


class CurrentUser(BaseModel):
    uid: str
    email: str | None = None


def require_auth_ready(settings: Settings = Depends(get_settings)) -> None:
    if not settings.auth_ready:
        raise HTTPException(503, "登入功能尚未設定完成")


def require_origin(request: Request, settings: Settings = Depends(get_settings)) -> None:
    if request.headers.get("origin") not in settings.allowed_origins:
        raise HTTPException(403, "不允許的請求來源")


def require_csrf(request: Request, _origin: None = Depends(require_origin)) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    if not cookie or not header or max(len(cookie), len(header)) > 128 or not secrets.compare_digest(cookie.encode(), header.encode()):
        raise HTTPException(403, "請重新載入頁面後再試一次")


def user_from_claims(claims: dict[str, Any], settings: Settings, request: Request) -> CurrentUser:
    uid = claims.get("uid")
    if not isinstance(uid, str) or not uid:
        raise HTTPException(401, "請重新登入")
    provider = claims.get("firebase")
    if not isinstance(provider, dict) or provider.get("sign_in_provider") != "google.com":
        raise HTTPException(403, "僅允許 Google 登入")
    if settings.admin_uid:
        from app.core.access import account_access_allowed
        allowed = account_access_allowed(request, uid, settings)
    else:
        allowed = uid in settings.allowed_uids
    if not allowed:
        raise HTTPException(403, "此帳號尚未獲得使用權限")
    return CurrentUser(uid=uid, email=claims.get("email"))


def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: AuthService = Depends(get_auth_service),
) -> CurrentUser:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        raise HTTPException(401, "請先登入")
    try:
        claims = service.verify_session_cookie(cookie)
    except InvalidCredential:
        raise HTTPException(401, "登入已失效，請重新登入") from None
    except Exception:
        raise HTTPException(503, "登入服務暫時無法使用") from None
    return user_from_claims(claims, settings, request)
