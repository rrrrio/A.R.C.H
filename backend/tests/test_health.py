"""Tests for the dependency-free Milestone 0 HTTP surface."""

from fastapi.testclient import TestClient

from local_voice_agent.main import app


def test_health_reports_service_status() -> None:
    """The health endpoint exposes a stable, non-sensitive readiness result."""
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "development"}
