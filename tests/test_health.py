from unittest.mock import AsyncMock

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from mintflow.config import Settings
from mintflow.main import create_app


def test_application_exposes_only_health_endpoints(settings: Settings) -> None:
    routes = {route.path for route in create_app(settings).routes if isinstance(route, APIRoute)}

    assert routes == {"/health/live", "/health/ready"}


@pytest.mark.anyio
async def test_liveness_does_not_require_dependencies(settings: Settings) -> None:
    transport = ASGITransport(app=create_app(settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
@pytest.mark.parametrize(("enabled", "status_code"), [(True, 200), (False, 404)])
async def test_api_docs_are_configurable(
    settings: Settings, enabled: bool, status_code: int
) -> None:
    configured_settings = settings.model_copy(update={"enable_api_docs": enabled})
    transport = ASGITransport(app=create_app(configured_settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/docs")

    assert response.status_code == status_code


@pytest.mark.parametrize(
    ("database_ready", "status_code", "status", "dependency_status"),
    [
        (True, 200, "ready", "available"),
        (False, 503, "not_ready", "unavailable"),
    ],
)
@pytest.mark.anyio
async def test_readiness_reports_postgresql_state(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    database_ready: bool,
    status_code: int,
    status: str,
    dependency_status: str,
) -> None:
    probe = AsyncMock(return_value=database_ready)
    monkeypatch.setattr("mintflow.main.is_postgresql_ready", probe)

    transport = ASGITransport(app=create_app(settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == status_code
    assert response.json() == {
        "status": status,
        "dependencies": {"postgresql": dependency_status},
    }
    probe.assert_awaited_once_with(settings.database_url.get_secret_value())
