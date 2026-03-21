"""Shared test fixtures for schwab-gateway.

No credentials or network access required — all Schwab HTTP calls are mocked.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


# ── Schwab candle/response helpers ────────────────────────────────────────────

def make_candle(
    ts_ms: int,
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
    volume: int = 1_000_000,
) -> dict:
    return {
        "datetime": ts_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def mock_schwab_resp(candles: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"candles": candles}
    return resp


def mock_schwab_client(candles: list[dict]) -> MagicMock:
    c = MagicMock()
    resp = mock_schwab_resp(candles)
    c.get_price_history.return_value = resp
    c.get_price_history_every_day.return_value = resp
    c.get_price_history_every_week.return_value = resp
    return c


# 2024-01-02 09:30 ET in epoch-ms
JAN2_0930_ET_MS: int = 1704205800000
# 2024-01-02 00:00 ET in epoch-ms
JAN2_MIDNIGHT_ET_MS: int = 1704153600000
# 2024-02-01 00:00 ET in epoch-ms
FEB1_MIDNIGHT_ET_MS: int = 1706745600000


# ── Token file helpers ─────────────────────────────────────────────────────────

def make_token_file(tmp_path: Path, age_seconds: float = 3600) -> Path:
    """Write a mock token.json to *tmp_path* and return its path."""
    now = time.time()
    token_path = tmp_path / "token.json"
    token_path.write_text(
        json.dumps(
            {
                "creation_timestamp": now - age_seconds,
                "token": {
                    "expires_in": 1800,
                    "token_type": "Bearer",
                    "scope": "api",
                    "refresh_token": "mock_refresh",
                    "access_token": "mock_access",
                    "id_token": "mock_id",
                    "expires_at": now + 1800,
                },
            }
        )
    )
    return token_path


# ── App fixture ────────────────────────────────────────────────────────────────

@pytest.fixture()
def test_app(tmp_path, monkeypatch):
    """Return a TestClient with a pre-initialised mock Schwab client."""
    token_path = make_token_file(tmp_path)

    # Patch settings so the app sees our tmp token path
    from gateway.settings import Settings
    monkeypatch.setattr(
        "gateway.settings._settings",
        Settings(
            schwab_app_key="test_key",
            schwab_api_secret="test_secret",
            schwab_callback_url="https://127.0.0.1",
            token_path=token_path,
        ),
    )

    # Patch client_state so no real schwab auth is attempted
    import gateway.client_state as cs
    monkeypatch.setattr(cs, "_client", MagicMock())

    from gateway.main import app
    return TestClient(app, raise_server_exceptions=True)
