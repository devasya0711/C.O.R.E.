"""Phase 1 API smoke tests."""

from app.main import app
from fastapi.testclient import TestClient


def test_health_returns_ok_without_external_services() -> None:
    """The public health route is usable before local infrastructure starts."""

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["components"]) == {"postgres", "neo4j", "redis"}
