import pytest
from pydantic import ValidationError

from app.schemas.preferences import UserPreferences


def test_reserved_preferences_defaults_and_mongo_identity():
    value = UserPreferences(user_uid="verified-uid")
    assert value.model_dump(by_alias=True) == {
        "_id": "verified-uid", "ui_language": "zh-TW", "ai_response_language": None,
    }


@pytest.mark.parametrize("language", ["zh-TW", "ja", "en", "th"])
def test_reserved_preferences_accept_supported_languages(language):
    value = UserPreferences(_id="verified-uid", ui_language=language, ai_response_language=language)
    assert value.ui_language == value.ai_response_language == language


@pytest.mark.parametrize("field", ["ui_language", "ai_response_language"])
def test_reserved_preferences_reject_unsupported_languages(field):
    with pytest.raises(ValidationError):
        UserPreferences.model_validate({"_id": "verified-uid", field: "unsupported"})
