from functools import lru_cache
from typing import Any, Protocol

from fastapi import Depends, HTTPException

from app.core.config import Settings, get_settings


class InvalidCredential(Exception):
    pass


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

    def _verify(self, method: Any, token: str) -> dict[str, Any]:
        try:
            return method(token, app=self.app, check_revoked=True)
        except (
            ValueError,
            self.auth.InvalidIdTokenError,
            self.auth.InvalidSessionCookieError,
            self.auth.RevokedIdTokenError,
            self.auth.RevokedSessionCookieError,
            self.auth.UserDisabledError,
            self.auth.UserNotFoundError,
        ) as exc:
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
        return self._verify(self.auth.verify_id_token, token)

    def create_session_cookie(self, token: str, seconds: int) -> str:
        cookie = self.auth.create_session_cookie(token, expires_in=seconds, app=self.app)
        return cookie.decode("utf-8") if isinstance(cookie, bytes) else cookie

    def verify_session_cookie(self, cookie: str) -> dict[str, Any]:
        return self._verify(self.auth.verify_session_cookie, cookie)


@lru_cache
def firebase_service(project_id: str) -> FirebaseAuthService:
    return FirebaseAuthService(project_id)


def get_auth_service(settings: Settings = Depends(get_settings)) -> AuthService:
    if not settings.auth_ready:
        raise HTTPException(503, "登入功能尚未設定完成")
    try:
        return firebase_service(settings.firebase_project_id)
    except Exception:
        raise HTTPException(503, "登入服務暫時無法使用") from None
