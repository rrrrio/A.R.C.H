"""Tests for the dependency-free Milestone 0 HTTP surface."""

from fastapi.testclient import TestClient

from arch.main import app


def test_health_reports_service_status() -> None:
    """The health endpoint exposes a stable, non-sensitive readiness result."""
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "development"}


def test_ready_reports_completed_application_startup() -> None:
    """The readiness route is a stable, non-sensitive gate for local service managers."""
    with TestClient(app) as client:
        response = client.get("/api/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
