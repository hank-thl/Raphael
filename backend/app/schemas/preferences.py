"""Reserved user_preferences document shape; no API or database writes yet."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

InterfaceLanguage = Literal["zh-TW", "ja", "en", "th"]


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Future persistence must derive this UID from the verified session.
    user_uid: str = Field(alias="_id", min_length=1)
    ui_language: InterfaceLanguage = "zh-TW"
    # None reserves automatic language selection; no AI behavior is implemented.
    ai_response_language: InterfaceLanguage | None = None
