"""Tests for GET /reauth, POST /reauth/complete, /reauth/status, and /reauth/ui."""
import base64
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

import gateway.routers.auth as auth_mod


def _write_token(path: Path, age_seconds: float = 3600) -> None:
    now = time.time()
    path.write_text(json.dumps({
        "creation_timestamp": now - age_seconds,
        "token": {
            "expires_in": 1800,
            "token_type": "Bearer",
            "scope": "api",
            "refresh_token": "r",
            "access_token": "a",
            "id_token": "i",
            "expires_at": now + 1800,
        },
    }))


def _set_pending_state(state: str | None) -> None:
    auth_mod._pending_state = state


class TestGetReauth:
    def test_returns_auth_url(self, test_app):
        resp = test_app.get("/reauth")
        assert resp.status_code == 200
        assert "auth_url" in resp.json()

    def test_auth_url_points_to_schwab(self, test_app):
        resp = test_app.get("/reauth")
        assert "schwabapi.com" in resp.json()["auth_url"]

    def test_auth_url_contains_required_params(self, test_app):
        resp = test_app.get("/reauth")
        url = resp.json()["auth_url"]
        assert "response_type=code" in url
        assert "client_id=test_key" in url
        assert "state=" in url

    def test_each_call_generates_a_fresh_state(self, test_app):
        url1 = test_app.get("/reauth").json()["auth_url"]
        url2 = test_app.get("/reauth").json()["auth_url"]
        # Extract state values and confirm they differ
        state1 = dict(p.split("=") for p in url1.split("?", 1)[1].split("&")).get("state")
        state2 = dict(p.split("=") for p in url2.split("?", 1)[1].split("&")).get("state")
        assert state1 != state2


class TestPostReauthComplete:
    # ------------------------------------------------------------------
    # Guard rails
    # ------------------------------------------------------------------

    def test_no_pending_state_returns_400(self, test_app):
        _set_pending_state(None)
        resp = test_app.post(
            "/reauth/complete",
            json={"callback_url": "https://127.0.0.1?code=abc&state=xyz"},
        )
        assert resp.status_code == 400
        assert "GET /reauth first" in resp.json()["detail"]

    def test_state_mismatch_returns_400(self, test_app):
        _set_pending_state("correct_state")
        resp = test_app.post(
            "/reauth/complete",
            json={"callback_url": "https://127.0.0.1?code=abc&state=wrong_state"},
        )
        assert resp.status_code == 400
        assert "State mismatch" in resp.json()["detail"]
        assert "correct_state" not in resp.json()["detail"]

    def test_missing_code_returns_400(self, test_app):
        _set_pending_state("some_state")
        resp = test_app.post(
            "/reauth/complete",
            json={"callback_url": "https://127.0.0.1?state=some_state"},
        )
        assert resp.status_code == 400
        assert "'code'" in resp.json()["detail"]

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def _mock_token_response(self) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "id_token": "new_id",
            "expires_in": 1800,
            "token_type": "Bearer",
            "scope": "api",
        }
        return mock_resp

    def test_successful_exchange_returns_ok(self, test_app, monkeypatch):
        test_app.get("/reauth")
        state = auth_mod._pending_state

        monkeypatch.setattr(
            "gateway.routers.auth.httpx.post", lambda *a, **kw: self._mock_token_response()
        )

        resp = test_app.post(
            "/reauth/complete",
            json={"callback_url": f"https://127.0.0.1?code=authcode123&state={state}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_pending_state_cleared_after_success(self, test_app, monkeypatch):
        test_app.get("/reauth")
        state = auth_mod._pending_state

        monkeypatch.setattr(
            "gateway.routers.auth.httpx.post", lambda *a, **kw: self._mock_token_response()
        )

        test_app.post(
            "/reauth/complete",
            json={"callback_url": f"https://127.0.0.1?code=code&state={state}"},
        )
        assert auth_mod._pending_state is None

    def test_token_file_written_after_success(self, test_app, monkeypatch):
        from gateway.settings import get_settings

        test_app.get("/reauth")
        state = auth_mod._pending_state

        monkeypatch.setattr(
            "gateway.routers.auth.httpx.post", lambda *a, **kw: self._mock_token_response()
        )

        test_app.post(
            "/reauth/complete",
            json={"callback_url": f"https://127.0.0.1?code=code&state={state}"},
        )

        token_path = get_settings().token_path
        assert token_path.exists()

    def test_basic_auth_header_is_base64_encoded_key_colon_secret(
        self, test_app, monkeypatch
    ):
        """Credentials must be base64(app_key:api_secret) per OAuth2 spec."""
        test_app.get("/reauth")
        state = auth_mod._pending_state

        captured: dict = {}

        def capture_post(url, headers=None, **kwargs):
            captured["auth"] = headers.get("Authorization", "")
            return self._mock_token_response()

        monkeypatch.setattr("gateway.routers.auth.httpx.post", capture_post)

        test_app.post(
            "/reauth/complete",
            json={"callback_url": f"https://127.0.0.1?code=code&state={state}"},
        )

        expected = base64.b64encode(b"test_key:test_secret").decode()
        assert captured["auth"] == f"Basic {expected}"

    # ------------------------------------------------------------------
    # Error paths
    # ------------------------------------------------------------------

    def test_schwab_http_error_returns_502(self, test_app, monkeypatch):
        test_app.get("/reauth")
        state = auth_mod._pending_state

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        def raise_status_error(*args, **kwargs):
            raise httpx.HTTPStatusError(
                "401", request=MagicMock(), response=mock_response
            )

        monkeypatch.setattr("gateway.routers.auth.httpx.post", raise_status_error)

        resp = test_app.post(
            "/reauth/complete",
            json={"callback_url": f"https://127.0.0.1?code=badcode&state={state}"},
        )
        assert resp.status_code == 502
        assert "token exchange failed" in resp.json()["detail"].lower()

    def test_network_error_returns_502(self, test_app, monkeypatch):
        test_app.get("/reauth")
        state = auth_mod._pending_state

        def raise_request_error(*args, **kwargs):
            raise httpx.RequestError("connection refused")

        monkeypatch.setattr("gateway.routers.auth.httpx.post", raise_request_error)

        resp = test_app.post(
            "/reauth/complete",
            json={"callback_url": f"https://127.0.0.1?code=code&state={state}"},
        )
        assert resp.status_code == 502
        assert "request failed" in resp.json()["detail"].lower()


class TestReauthStatus:
    def test_ok_when_freshly_created(self, test_app):
        # test_app fixture writes a token 1 hour old — well inside the 7-day window
        resp = test_app.get("/reauth/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["remaining_days"] > 6

    def test_expiring_within_alert_threshold(self, test_app):
        from gateway.settings import get_settings

        settings = get_settings()
        _write_token(settings.token_path, age_seconds=7 * 86_400 - 3600)  # 1h left

        resp = test_app.get("/reauth/status")
        assert resp.json()["status"] == "expiring"
        assert 0 < resp.json()["remaining_days"] < settings.alert_threshold_days

    def test_expired(self, test_app):
        from gateway.settings import get_settings

        settings = get_settings()
        _write_token(settings.token_path, age_seconds=8 * 86_400)  # 1 day past TTL

        resp = test_app.get("/reauth/status")
        assert resp.json()["status"] == "expired"
        assert resp.json()["remaining_days"] < 0

    def test_missing_token_file(self, test_app):
        from gateway.settings import get_settings

        get_settings().token_path.unlink()

        resp = test_app.get("/reauth/status")
        assert resp.json() == {"status": "missing", "remaining_days": None}

    def test_unreadable_token_file(self, test_app):
        from gateway.settings import get_settings

        get_settings().token_path.write_text("not json")

        resp = test_app.get("/reauth/status")
        assert resp.json() == {"status": "missing", "remaining_days": None}


class TestReauthUi:
    def test_returns_html_page(self, test_app):
        resp = test_app.get("/reauth/ui")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Start login" in resp.text
        assert "/reauth/status" in resp.text
        assert "/reauth/complete" in resp.text
