"""Public health contract, including missing and failed infrastructure."""

from unittest.mock import AsyncMock

import pytest
from app.api.v1 import health as health_module
from app.api.v1.health import ComponentHealth
from app.core.auth import get_current_user
from app.core.config import Settings
from app.main import create_app
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


def test_health_returns_200_when_all_stores_respond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("_probe_postgres", "_probe_neo4j", "_probe_redis"):
        monkeypatch.setattr(
            health_module,
            name,
            AsyncMock(
                return_value=ComponentHealth(
                    status="healthy", latency_ms=2.5, detail=f"{name} ok"
                )
            ),
        )
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["postgres_ms"] == 2.5
    assert payload["neo4j_ms"] == 2.5
    assert payload["redis_ms"] == 2.5
    assert payload["components"]["postgres"]["detail"] == "_probe_postgres ok"


def test_missing_configuration_is_not_reported_as_healthy() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 503
    components = response.json()["error"]["details"]["components"]
    assert set(components) == {"postgres", "neo4j", "redis"}
    assert all(item["status"] == "not_configured" for item in components.values())
    assert all(item["detail"] == "not configured" for item in components.values())


def test_one_unhealthy_store_returns_503_with_component_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        health_module,
        "_probe_postgres",
        AsyncMock(
            return_value=ComponentHealth(
                status="healthy", latency_ms=1.25, detail="postgres ok"
            )
        ),
    )
    monkeypatch.setattr(
        health_module,
        "_probe_neo4j",
        AsyncMock(
            return_value=ComponentHealth(
                status="unhealthy", latency_ms=25.5, detail="neo4j unavailable"
            )
        ),
    )
    monkeypatch.setattr(
        health_module,
        "_probe_redis",
        AsyncMock(
            return_value=ComponentHealth(
                status="healthy", latency_ms=3.75, detail="redis ok"
            )
        ),
    )

    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 503
    details = response.json()["error"]["details"]
    assert details["status"] == "degraded"
    assert details["neo4j_ms"] == 25.5
    assert details["components"]["neo4j"] == {
        "status": "unhealthy",
        "latency_ms": 25.5,
        "detail": "neo4j unavailable",
    }


def test_protected_endpoint_rejects_missing_token_without_store_access() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.put(
            "/api/v1/settings/github-token", json={"token": "test-only"}
        )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_cors_allows_only_configured_origins() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        origins = [("http://localhost:3000", 200), ("https://evil.test", 400)]
        for origin, expected in origins:
            response = client.options(
                "/api/v1/settings/github-token",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "PUT",
                    "Access-Control-Request-Headers": "authorization",
                },
            )
            assert response.status_code == expected
            if expected == 200:
                assert response.headers["access-control-allow-origin"] == origin
            else:
                assert "access-control-allow-origin" not in response.headers


def test_all_resource_api_routes_require_server_identity() -> None:
    application = create_app(Settings(_env_file=None))
    for route in application.routes:
        if isinstance(route, APIRoute) and route.path != "/api/v1/health":
            assert get_current_user in {
                dependency.call for dependency in route.dependant.dependencies
            }, route.path
