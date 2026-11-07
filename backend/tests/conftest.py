import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def no_real_mongodb_in_tests(monkeypatch):
    # Even when pytest is launched with --env-file, never connect to Atlas.
    monkeypatch.setenv("MONGODB_URI", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
