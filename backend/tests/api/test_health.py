"""Tests for the /health endpoint and session-token authentication."""

import pytest
from fastapi.testclient import TestClient

from invoice_renamer.api.app import create_app

TOKEN = "test-session-token"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("INVOICE_RENAMER_SESSION_TOKEN", TOKEN)
    return TestClient(create_app())


def test_health_with_valid_token_returns_ok(client: TestClient) -> None:
    response = client.get("/health", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_without_token_is_unauthorized(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 401


def test_health_with_wrong_token_is_unauthorized(client: TestClient) -> None:
    response = client.get("/health", headers={"Authorization": "Bearer wrong-token"})

    assert response.status_code == 401


def test_health_missing_server_token_env_var_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INVOICE_RENAMER_SESSION_TOKEN", raising=False)
    unconfigured_client = TestClient(create_app(), raise_server_exceptions=False)

    response = unconfigured_client.get("/health", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 500
