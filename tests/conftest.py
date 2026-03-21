"""Shared test fixtures for schwab-gateway.

No credentials or network access required — all Schwab HTTP calls are mocked.
"""
from __future__ import annotations

import inspect

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


# ── MockStreamClient ───────────────────────────────────────────────────────────

class MockStreamClient:
    """Minimal StreamClient stand-in for tests.

    Pass ``canned_messages`` as a list of ``(service_key, message_dict)`` pairs.
    Each call to ``handle_message()`` dispatches one canned message to the
    registered handler for that service, then raises ``WebSocketDisconnect``
    when the list is exhausted.
    """

    def __init__(self, http_client=None, *, canned_messages=None):
        self._handlers: dict[str, list] = {}
        self._call_count = 0
        self._canned: list[tuple[str, dict]] = canned_messages or []
        self.logout_called = False

    async def login(self) -> None:
        pass

    async def logout(self) -> None:
        self.logout_called = True

    # -- subscription methods (all no-ops) --
    async def level_one_equity_subs(self, symbols, *, fields=None): pass
    async def chart_equity_subs(self, symbols): pass
    async def level_one_option_subs(self, symbols, *, fields=None): pass
    async def level_one_futures_subs(self, symbols, *, fields=None): pass
    async def chart_futures_subs(self, symbols): pass
    async def level_one_forex_subs(self, symbols, *, fields=None): pass
    async def level_one_futures_options_subs(self, symbols, *, fields=None): pass
    async def nyse_book_subs(self, symbols): pass
    async def nasdaq_book_subs(self, symbols): pass
    async def options_book_subs(self, symbols): pass
    async def screener_equity_subs(self, symbols): pass
    async def screener_option_subs(self, symbols): pass
    async def account_activity_sub(self): pass

    # -- handler registration --
    def _register(self, service_key: str, handler) -> None:
        self._handlers.setdefault(service_key, []).append(handler)

    def add_level_one_equity_handler(self, h): self._register("LEVELONE_EQUITIES", h)
    def add_chart_equity_handler(self, h): self._register("CHART_EQUITY", h)
    def add_level_one_option_handler(self, h): self._register("LEVELONE_OPTIONS", h)
    def add_level_one_futures_handler(self, h): self._register("LEVELONE_FUTURES", h)
    def add_chart_futures_handler(self, h): self._register("CHART_FUTURES", h)
    def add_level_one_forex_handler(self, h): self._register("LEVELONE_FOREX", h)
    def add_level_one_futures_options_handler(self, h): self._register("LEVELONE_FUTURES_OPTIONS", h)
    def add_nyse_book_handler(self, h): self._register("NYSE_BOOK", h)
    def add_nasdaq_book_handler(self, h): self._register("NASDAQ_BOOK", h)
    def add_options_book_handler(self, h): self._register("OPTIONS_BOOK", h)
    def add_screener_equity_handler(self, h): self._register("SCREENER_EQUITY", h)
    def add_screener_option_handler(self, h): self._register("SCREENER_OPTION", h)
    def add_account_activity_handler(self, h): self._register("ACCT_ACTIVITY", h)

    async def handle_message(self) -> None:
        from fastapi import WebSocketDisconnect
        if self._call_count >= len(self._canned):
            raise WebSocketDisconnect()
        service_key, msg = self._canned[self._call_count]
        self._call_count += 1
        for handler in self._handlers.get(service_key, []):
            result = handler(msg)
            if inspect.isawaitable(result):
                await result


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
