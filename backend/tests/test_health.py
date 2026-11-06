from fastapi.testclient import TestClient

from app.main import app


def test_health_is_public_and_has_no_external_service_dependencies() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_record_api_is_not_available_before_authentication_is_implemented() -> None:
    with TestClient(app) as client:
        assert client.get("/records").status_code == 404
        assert client.post("/records", json={"content": "test"}).status_code == 404
        assert client.delete("/records/example").status_code == 404
