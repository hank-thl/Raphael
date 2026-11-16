"""AI feature access is independent of account administration."""
from fastapi import Depends, HTTPException

from app.core.config import get_settings
from app.core.security import get_current_user


def ai_user_allowed(uid, settings):
    return uid in settings.ai_user_uids


def require_ai_user(user=Depends(get_current_user), settings=Depends(get_settings)):
    # get_current_user also enforces the dynamic account enabled/disabled state.
    if not ai_user_allowed(user.uid, settings):
        raise HTTPException(403, '此帳號尚未開放 AI 功能')
    return user
