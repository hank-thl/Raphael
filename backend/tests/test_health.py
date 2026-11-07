from fastapi.testclient import TestClient

from app.main import app


def test_health_is_public_and_has_no_external_service_dependencies() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
