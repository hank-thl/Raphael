import logging
from functools import lru_cache
from typing import Any, Protocol

from fastapi import Depends, HTTPException

from app.core.config import Settings, get_settings


logger = logging.getLogger(__name__)

# Bound minor issuer/server drift; Firebase supports 0–60 seconds.
CLOCK_SKEW_SECONDS = 5


class InvalidCredential(Exception):
    pass


def credential_failure_reason(error: Exception) -> str:
    """Return a fixed diagnostic label; never log the SDK's raw token error text."""
    name = type(error).__name__
    reasons = {
        "ExpiredIdTokenError": "expired",
        "ExpiredSessionCookieError": "expired",
        "RevokedIdTokenError": "revoked",
        "RevokedSessionCookieError": "revoked",
        "UserDisabledError": "user_disabled",
        "UserNotFoundError": "user_not_found",
    }
    if name in reasons:
        return reasons[name]
    detail = str(error).lower()
    if "token used too early" in detail or "not yet valid" in detail:
        return "clock_skew"
    if 'incorrect "aud"' in detail or 'incorrect "iss"' in detail:
        return "project_mismatch"
    if "signature" in detail:
        return "invalid_signature"
    return "invalid_token"


class AuthService(Protocol):
    def verify_id_token(self, token: str) -> dict[str, Any]: ...
    def create_session_cookie(self, token: str, seconds: int) -> str: ...
    def verify_session_cookie(self, cookie: str) -> dict[str, Any]: ...


class FirebaseAuthService:
    def __init__(self, project_id: str):
        # Lazy import keeps /health independent of Firebase setup.
        import firebase_admin
        from firebase_admin import auth, credentials

        self.auth = auth
        try:
            self.app = firebase_admin.get_app("raphael")
        except ValueError:
            credential = credentials.ApplicationDefault()
            credential.get_credential()
            self.app = firebase_admin.initialize_app(
                credential, {"projectId": project_id}, name="raphael"
            )

    def _verify(self, method: Any, token: str, stage: str) -> dict[str, Any]:
        try:
            return method(token, app=self.app, check_revoked=True, clock_skew_seconds=CLOCK_SKEW_SECONDS)
        except (
            ValueError,
            self.auth.InvalidIdTokenError,
            self.auth.InvalidSessionCookieError,
            self.auth.RevokedIdTokenError,
            self.auth.RevokedSessionCookieError,
            self.auth.UserDisabledError,
            self.auth.UserNotFoundError,
        ) as exc:
            logger.warning("Authentication rejected stage=%s reason=%s exception=%s",
                           stage, credential_failure_reason(exc), type(exc).__name__)
            raise InvalidCredential from exc

    def ensure_account(self, email: str):
        try:
            user = self.auth.get_user_by_email(email, app=self.app)
        except self.auth.UserNotFoundError:
            try:
                user = self.auth.create_user(email=email, email_verified=False, app=self.app)
            except self.auth.EmailAlreadyExistsError:
                user = self.auth.get_user_by_email(email, app=self.app)
        if user.disabled:
            raise ValueError("Firebase account disabled")
        return user

    def verify_id_token(self, token: str) -> dict[str, Any]:
        return self._verify(self.auth.verify_id_token, token, "verify_id_token")

    def create_session_cookie(self, token: str, seconds: int) -> str:
        cookie = self.auth.create_session_cookie(token, expires_in=seconds, app=self.app)
        return cookie.decode("utf-8") if isinstance(cookie, bytes) else cookie

    def verify_session_cookie(self, cookie: str) -> dict[str, Any]:
        return self._verify(self.auth.verify_session_cookie, cookie, "verify_session_cookie")


@lru_cache
def firebase_service(project_id: str) -> FirebaseAuthService:
    return FirebaseAuthService(project_id)


def get_auth_service(settings: Settings = Depends(get_settings)) -> AuthService:
    if not settings.auth_ready:
        logger.warning("Authentication unavailable stage=configuration")
        raise HTTPException(503, "登入功能尚未設定完成")
    try:
        return firebase_service(settings.firebase_project_id)
    except Exception as exc:
        logger.warning("Authentication unavailable stage=firebase_initialize exception=%s", type(exc).__name__)
        raise HTTPException(503, "登入服務暫時無法使用") from None
