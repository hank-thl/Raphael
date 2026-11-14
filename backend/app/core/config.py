import os
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    firebase_project_id: str = ""
    allowed_uids: frozenset[str] = frozenset()
    admin_uid: str = ""
    allowed_origins: frozenset[str] = frozenset()
    signup_restriction_confirmed: bool = False
    cookie_secure: bool = True
    session_seconds: int = 1209600
    mongodb_uri: str = field(default="", repr=False)
    mongodb_database: str = "raphael_dev"
    gemini_api_key: str = field(default="", repr=False)
    gemini_model: str = ""
    ai_capture_enabled: bool = False

    @property
    def auth_ready(self) -> bool:
        return bool(
            self.firebase_project_id
            and (self.allowed_uids or self.admin_uid)
            and self.allowed_origins
            and self.signup_restriction_confirmed
        )


@lru_cache
def get_settings() -> Settings:
    session_seconds = int(os.getenv("SESSION_SECONDS", "1209600"))
    if not 300 <= session_seconds <= 1209600:
        raise ValueError("SESSION_SECONDS must be between 300 and 1209600")
    return Settings(
        admin_uid=os.getenv("ADMIN_FIREBASE_UID", "").strip(),
        firebase_project_id=os.getenv("FIREBASE_PROJECT_ID", "").strip(),
        allowed_uids=frozenset(filter(None, (s.strip() for s in os.getenv("ALLOWED_FIREBASE_UIDS", "").split(",")))),
        allowed_origins=frozenset(filter(None, (s.strip() for s in os.getenv("AUTH_ALLOWED_ORIGINS", "").split(",")))),
        signup_restriction_confirmed=os.getenv("AUTH_SIGNUP_RESTRICTION_CONFIRMED", "false").lower() == "true",
        cookie_secure=os.getenv("COOKIE_SECURE", "true").lower() == "true",
        session_seconds=session_seconds,
        mongodb_uri=os.getenv("MONGODB_URI", "").strip(),
        mongodb_database=os.getenv("MONGODB_DATABASE", "raphael_dev").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "").strip(),
        ai_capture_enabled=os.getenv("AI_CAPTURE_ENABLED", "false").lower() == "true",
    )
