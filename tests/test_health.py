"""Tests for GET /health."""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_fresh_token(tmp_path: Path) -> Path:
    """Write a token that is 1 hour old."""
    now = time.time()
    p = tmp_path / "token.json"
    p.write_text(
        json.dumps(
            {
                "creation_timestamp": now - 3600,
                "token": {
                    "expires_in": 1800,
                    "token_type": "Bearer",
                    "scope": "api",
                    "refresh_token": "r",
                    "access_token": "a",
                    "id_token": "i",
                    "expires_at": now + 1800,
                },
            }
        )
    )
    return p


class TestHealth:
    def test_returns_200(self, test_app):
        resp = test_app.get("/health")
        assert resp.status_code == 200

    def test_status_ok_when_client_ready(self, test_app):
        data = test_app.get("/health").json()
        assert data["status"] == "ok"
        assert data["client_ready"] is True

    def test_token_fields_present(self, test_app):
        data = test_app.get("/health").json()
        token = data["token"]
        assert "age_hours" in token
        assert "refresh_expires_in_hours" in token
        assert "refresh_expires_at" in token

    def test_age_hours_is_non_negative(self, test_app):
        data = test_app.get("/health").json()
        assert data["token"]["age_hours"] >= 0

    def test_degraded_when_client_not_ready(self, test_app, monkeypatch):
        import gateway.client_state as cs
        monkeypatch.setattr(cs, "_client", None)
        data = test_app.get("/health").json()
        assert data["status"] == "degraded"
        assert data["client_ready"] is False

    def test_token_load_error_returns_error_key(self, tmp_path, monkeypatch):
        """When token file exists but is corrupt, token dict should contain 'error'."""
        from gateway.settings import Settings
        import gateway.client_state as cs

        token_path = tmp_path / "bad_token.json"
        token_path.write_text("not valid json {{{")

        monkeypatch.setattr(
            "gateway.settings._settings",
            Settings(
                schwab_app_key="k",
                schwab_api_secret="s",
                schwab_callback_url="https://127.0.0.1",
                token_path=token_path,
            ),
        )
        monkeypatch.setattr(cs, "_client", MagicMock())

        from gateway.main import app
        client = TestClient(app, raise_server_exceptions=True)
        data = client.get("/health").json()
        assert "error" in data["token"]

    def test_token_error_when_file_missing(self, tmp_path, monkeypatch):
        from gateway.settings import Settings
        import gateway.client_state as cs

        monkeypatch.setattr(
            "gateway.settings._settings",
            Settings(
                schwab_app_key="k",
                schwab_api_secret="s",
                schwab_callback_url="https://127.0.0.1",
                token_path=tmp_path / "nonexistent.json",
            ),
        )
        monkeypatch.setattr(cs, "_client", None)

        from gateway.main import app
        client = TestClient(app, raise_server_exceptions=True)
        data = client.get("/health").json()
        assert "error" in data["token"]
