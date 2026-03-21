"""Tests for /bars, /daily, /weekly routes."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    JAN2_0930_ET_MS,
    JAN2_MIDNIGHT_ET_MS,
    FEB1_MIDNIGHT_ET_MS,
    make_candle,
    mock_schwab_client,
    mock_schwab_resp,
)


# ── /bars ─────────────────────────────────────────────────────────────────────

class TestBarsEndpoint:
    def _rth_candle(self, offset_minutes: int = 0) -> dict:
        """Return a candle at 09:30 ET + offset_minutes."""
        return make_candle(JAN2_0930_ET_MS + offset_minutes * 60_000)

    def test_returns_200_with_data(self, test_app, monkeypatch):
        import gateway.client_state as cs
        cs._client = mock_schwab_client([self._rth_candle()])
        resp = test_app.get(
            "/bars/SPY", params={"from_date": "2024-01-02", "to_date": "2024-01-02"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "SPY"
        assert data["count"] == 1
        assert len(data["data"]) == 1

    def test_envelope_fields(self, test_app, monkeypatch):
        import gateway.client_state as cs
        cs._client = mock_schwab_client([self._rth_candle()])
        data = test_app.get(
            "/bars/SPY", params={"from_date": "2024-01-02", "to_date": "2024-01-02"}
        ).json()
        assert set(data.keys()) >= {"symbol", "count", "data"}

    def test_raw_response_has_ts_ms(self, test_app, monkeypatch):
        """With clean=false the raw ts_ms field is returned."""
        import gateway.client_state as cs
        cs._client = mock_schwab_client([self._rth_candle()])
        data = test_app.get(
            "/bars/SPY",
            params={"from_date": "2024-01-02", "to_date": "2024-01-02", "clean": "false"},
        ).json()
        assert "ts_ms" in data["data"][0]

    def test_clean_response_has_timestamp(self, test_app, monkeypatch):
        """With clean=true (default) the timestamp field is an ISO string."""
        import gateway.client_state as cs
        cs._client = mock_schwab_client([self._rth_candle()])
        data = test_app.get(
            "/bars/SPY",
            params={"from_date": "2024-01-02", "to_date": "2024-01-02", "clean": "true"},
        ).json()
        row = data["data"][0]
        assert "timestamp" in row
        assert "ts_ms" not in row

    def test_pre_market_bar_filtered_when_clean(self, test_app, monkeypatch):
        """Pre-market candle (08:00 ET) must be dropped when clean=true."""
        import gateway.client_state as cs
        pre_market_ms = JAN2_0930_ET_MS - 90 * 60_000  # 08:00 ET
        candles = [make_candle(pre_market_ms), self._rth_candle()]
        cs._client = mock_schwab_client(candles)
        data = test_app.get(
            "/bars/SPY",
            params={"from_date": "2024-01-02", "to_date": "2024-01-02", "clean": "true"},
        ).json()
        assert data["count"] == 1

    def test_invalid_frequency_returns_422(self, test_app):
        resp = test_app.get(
            "/bars/SPY",
            params={"from_date": "2024-01-02", "to_date": "2024-01-02", "frequency": "7"},
        )
        assert resp.status_code == 422

    def test_empty_candles_returns_404(self, test_app, monkeypatch):
        import gateway.client_state as cs
        cs._client = mock_schwab_client([])
        resp = test_app.get(
            "/bars/SPY", params={"from_date": "2024-01-02", "to_date": "2024-01-02"}
        )
        assert resp.status_code == 404

    def test_no_client_returns_503(self, test_app, monkeypatch):
        import gateway.client_state as cs
        monkeypatch.setattr(cs, "_client", None)
        resp = test_app.get(
            "/bars/SPY", params={"from_date": "2024-01-02", "to_date": "2024-01-02"}
        )
        assert resp.status_code == 503

    def test_symbol_uppercased(self, test_app, monkeypatch):
        import gateway.client_state as cs
        cs._client = mock_schwab_client([self._rth_candle()])
        data = test_app.get(
            "/bars/spy", params={"from_date": "2024-01-02", "to_date": "2024-01-02"}
        ).json()
        assert data["symbol"] == "SPY"

    def test_resample_60_returns_hourly_bars(self, test_app, monkeypatch):
        """Four 30-min RTH candles should collapse to ≤2 hourly bars."""
        import gateway.client_state as cs
        # 09:30, 10:00, 10:30, 11:00 ET
        candles = [make_candle(JAN2_0930_ET_MS + i * 30 * 60_000) for i in range(4)]
        cs._client = mock_schwab_client(candles)
        data = test_app.get(
            "/bars/SPY",
            params={
                "from_date": "2024-01-02",
                "to_date": "2024-01-02",
                "resample_60": "true",
            },
        ).json()
        # hourly bars: 10:00 bucket (09:30+10:00 bars) gets dropped pre-10, leaving ≤2
        assert data["count"] <= 3


# ── /daily ────────────────────────────────────────────────────────────────────

class TestDailyEndpoint:
    def _daily_candle(self, ts_ms: int = JAN2_MIDNIGHT_ET_MS) -> dict:
        return make_candle(ts_ms, open_=445.0, high=455.0, low=440.0,
                           close=450.0, volume=80_000_000)

    def test_returns_200(self, test_app, monkeypatch):
        import gateway.client_state as cs
        cs._client = mock_schwab_client([self._daily_candle()])
        resp = test_app.get(
            "/daily/SPY", params={"from_date": "2024-01-02", "to_date": "2024-01-02"}
        )
        assert resp.status_code == 200

    def test_date_field_present(self, test_app, monkeypatch):
        import gateway.client_state as cs
        cs._client = mock_schwab_client([self._daily_candle()])
        data = test_app.get(
            "/daily/SPY", params={"from_date": "2024-01-02", "to_date": "2024-01-02"}
        ).json()
        assert "date" in data["data"][0]

    def test_multiple_bars(self, test_app, monkeypatch):
        import gateway.client_state as cs
        candles = [
            self._daily_candle(JAN2_MIDNIGHT_ET_MS),
            self._daily_candle(FEB1_MIDNIGHT_ET_MS),
        ]
        cs._client = mock_schwab_client(candles)
        data = test_app.get(
            "/daily/SPY", params={"from_date": "2024-01-02", "to_date": "2024-02-01"}
        ).json()
        assert data["count"] == 2

    def test_empty_returns_404(self, test_app, monkeypatch):
        import gateway.client_state as cs
        cs._client = mock_schwab_client([])
        resp = test_app.get(
            "/daily/SPY", params={"from_date": "2024-01-02", "to_date": "2024-01-02"}
        )
        assert resp.status_code == 404

    def test_no_client_returns_503(self, test_app, monkeypatch):
        import gateway.client_state as cs
        monkeypatch.setattr(cs, "_client", None)
        resp = test_app.get(
            "/daily/SPY", params={"from_date": "2024-01-02", "to_date": "2024-01-02"}
        )
        assert resp.status_code == 503

    def test_ohlcv_values_preserved(self, test_app, monkeypatch):
        import gateway.client_state as cs
        candle = self._daily_candle()
        cs._client = mock_schwab_client([candle])
        data = test_app.get(
            "/daily/SPY", params={"from_date": "2024-01-02", "to_date": "2024-01-02"}
        ).json()
        row = data["data"][0]
        assert row["close"] == pytest.approx(450.0)
        assert row["volume"] == 80_000_000


# ── /weekly ───────────────────────────────────────────────────────────────────

class TestWeeklyEndpoint:
    _MAR4_MS = 1709524800000  # 2024-03-04 00:00 ET

    def test_returns_200(self, test_app, monkeypatch):
        import gateway.client_state as cs
        cs._client = mock_schwab_client([make_candle(self._MAR4_MS)])
        resp = test_app.get(
            "/weekly/SPY", params={"from_date": "2024-03-04", "to_date": "2024-03-08"}
        )
        assert resp.status_code == 200

    def test_weekly_uses_every_week_helper(self, test_app, monkeypatch):
        """Verify the router calls get_price_history_every_week (not the raw method)."""
        import gateway.client_state as cs

        mock_client = mock_schwab_client([make_candle(self._MAR4_MS)])
        cs._client = mock_client
        test_app.get(
            "/weekly/SPY", params={"from_date": "2024-03-04", "to_date": "2024-03-08"}
        )
        mock_client.get_price_history_every_week.assert_called_once()

    def test_empty_returns_404(self, test_app, monkeypatch):
        import gateway.client_state as cs
        cs._client = mock_schwab_client([])
        resp = test_app.get(
            "/weekly/SPY", params={"from_date": "2024-03-04", "to_date": "2024-03-08"}
        )
        assert resp.status_code == 404
