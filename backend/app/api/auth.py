import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, get_settings
from app.core.firebase import AuthService, InvalidCredential, get_auth_service
from app.core.security import (
    CSRF_COOKIE, SESSION_COOKIE, CurrentUser, get_current_user,
    require_auth_ready, require_csrf, user_from_claims,
)

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_auth_ready)])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id_token: str = Field(min_length=1, max_length=16384)


@router.get("/csrf")
def csrf(
    response: Response,
    settings: Settings = Depends(get_settings),
    _service: AuthService = Depends(get_auth_service),
) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    response.set_cookie(CSRF_COOKIE, token, max_age=3600, httponly=True,
                        secure=settings.cookie_secure, samesite="lax", path="/")
    return {"csrf_token": token}


@router.post("/login", response_model=CurrentUser, dependencies=[Depends(require_csrf)])
def login(
    body: LoginRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
    service: AuthService = Depends(get_auth_service),
) -> CurrentUser:
    try:
        claims = service.verify_id_token(body.id_token)
    except InvalidCredential:
        raise HTTPException(401, "登入已失效，請重新登入") from None
    except Exception:
        raise HTTPException(503, "登入服務暫時無法使用") from None
    user = user_from_claims(claims, settings)
    auth_time = claims.get("auth_time")
    now = time.time()
    if not isinstance(auth_time, (int, float)) or not now - 300 <= auth_time <= now + 30:
        raise HTTPException(401, "請重新執行 Google 登入")
    try:
        session = service.create_session_cookie(body.id_token, settings.session_seconds)
    except Exception:
        raise HTTPException(503, "登入服務暫時無法使用") from None
    response.set_cookie(SESSION_COOKIE, session, max_age=settings.session_seconds,
                        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")
    return user


@router.get("/me", response_model=CurrentUser)
def me(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    return user


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf)])
def logout(response: Response, settings: Settings = Depends(get_settings)) -> None:
    # Local logout only; other devices are not revoked.
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True,
                           secure=settings.cookie_secure, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", httponly=True,
                           secure=settings.cookie_secure, samesite="lax")
