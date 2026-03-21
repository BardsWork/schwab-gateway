"""Tests for WS /stream."""
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.client_state as cs
import gateway.routers.stream as stream_mod
from tests.conftest import MockStreamClient


def _patch_stream_client(monkeypatch, mock_sc: MockStreamClient) -> None:
    """Replace schwab.streaming.StreamClient in the stream router with a mock."""
    monkeypatch.setattr(
        stream_mod.schwab.streaming,
        "StreamClient",
        lambda *args, **kwargs: mock_sc,
    )


class TestStreamEndpoint:

    def test_503_when_no_client(self, test_app, monkeypatch):
        monkeypatch.setattr(cs, "_client", None)
        with test_app.websocket_connect("/stream") as ws:
            msg = ws.receive_json()
        assert msg["error"] == "no_client"

    def test_invalid_subscription_type_returns_error(self, test_app):
        cs._client = MagicMock()
        with test_app.websocket_connect("/stream") as ws:
            ws.send_json({"subscriptions": [{"type": "not_a_real_type", "symbols": ["SPY"]}]})
            msg = ws.receive_json()
        assert msg["error"] == "invalid_spec"
        assert "not_a_real_type" in msg["detail"]

    def test_missing_subscriptions_key_returns_error(self, test_app):
        cs._client = MagicMock()
        with test_app.websocket_connect("/stream") as ws:
            ws.send_json({"wrong_key": []})
            msg = ws.receive_json()
        assert msg["error"] == "invalid_spec"

    def test_missing_symbols_returns_error(self, test_app):
        cs._client = MagicMock()
        with test_app.websocket_connect("/stream") as ws:
            ws.send_json({"subscriptions": [{"type": "level_one_equity"}]})
            msg = ws.receive_json()
        assert msg["error"] == "invalid_spec"
        assert "symbols" in msg["detail"]

    def test_valid_subscription_receives_ack(self, test_app, monkeypatch):
        cs._client = MagicMock()
        mock_sc = MockStreamClient(canned_messages=[])
        _patch_stream_client(monkeypatch, mock_sc)

        with test_app.websocket_connect("/stream") as ws:
            ws.send_json({"subscriptions": [{"type": "level_one_equity", "symbols": ["SPY"]}]})
            ack = ws.receive_json()
        assert ack["status"] == "subscribed"
        assert ack["count"] == 1

    def test_canned_message_forwarded_to_client(self, test_app, monkeypatch):
        cs._client = MagicMock()
        canned_msg = {
            "service": "LEVELONE_EQUITIES",
            "timestamp": 1711022400000,
            "content": [{"key": "SPY", "BID_PRICE": 591.23}],
        }
        mock_sc = MockStreamClient(canned_messages=[("LEVELONE_EQUITIES", canned_msg)])
        _patch_stream_client(monkeypatch, mock_sc)

        with test_app.websocket_connect("/stream") as ws:
            ws.send_json({"subscriptions": [{"type": "level_one_equity", "symbols": ["SPY"]}]})
            _ack = ws.receive_json()
            data = ws.receive_json()

        assert data["service"] == "LEVELONE_EQUITIES"
        assert data["content"] == canned_msg

    def test_account_activity_requires_no_symbols(self, test_app, monkeypatch):
        cs._client = MagicMock()
        mock_sc = MockStreamClient(canned_messages=[])
        _patch_stream_client(monkeypatch, mock_sc)

        with test_app.websocket_connect("/stream") as ws:
            ws.send_json({"subscriptions": [{"type": "account_activity"}]})
            ack = ws.receive_json()
        assert ack["status"] == "subscribed"

    def test_multiple_subscriptions(self, test_app, monkeypatch):
        cs._client = MagicMock()
        mock_sc = MockStreamClient(canned_messages=[])
        _patch_stream_client(monkeypatch, mock_sc)

        with test_app.websocket_connect("/stream") as ws:
            ws.send_json({
                "subscriptions": [
                    {"type": "level_one_equity", "symbols": ["SPY"]},
                    {"type": "chart_equity", "symbols": ["SPY"]},
                    {"type": "account_activity"},
                ]
            })
            ack = ws.receive_json()
        assert ack["count"] == 3

    def test_logout_called_on_disconnect(self, test_app, monkeypatch):
        cs._client = MagicMock()
        mock_sc = MockStreamClient(canned_messages=[])
        _patch_stream_client(monkeypatch, mock_sc)

        with test_app.websocket_connect("/stream") as ws:
            ws.send_json({"subscriptions": [{"type": "level_one_equity", "symbols": ["SPY"]}]})
            ws.receive_json()  # ack
        # After context exits (disconnect), logout should have been called
        assert mock_sc.logout_called


class TestStreamReconnect:
    """Reconnect behaviour when the Schwab-side stream breaks."""

    _SUB = {"subscriptions": [{"type": "level_one_equity", "symbols": ["SPY"]}]}

    def _no_sleep(self, monkeypatch) -> None:
        """Patch asyncio.sleep in the stream module so tests don't stall."""
        monkeypatch.setattr(stream_mod.asyncio, "sleep", AsyncMock())

    def _make_clients(self, monkeypatch, clients: list) -> None:
        """Patch StreamClient constructor to return successive mock clients."""
        idx = [0]
        def factory(*args, **kwargs):
            c = clients[min(idx[0], len(clients) - 1)]
            idx[0] += 1
            return c
        monkeypatch.setattr(stream_mod.schwab.streaming, "StreamClient", factory)

    # ------------------------------------------------------------------
    # Forwarder behaviour
    # ------------------------------------------------------------------

    def test_forwarder_logs_warning_and_raises_fe_disconnected(self, caplog):
        """_make_forwarder should log and raise _FEDisconnected, not silently pass."""
        import asyncio
        import logging

        mock_ws = MagicMock()
        mock_ws.send_json = AsyncMock(side_effect=RuntimeError("socket closed"))

        forwarder = stream_mod._make_forwarder(mock_ws, "LEVELONE_EQUITIES")

        with caplog.at_level(logging.WARNING, logger="gateway.routers.stream"):
            with pytest.raises(stream_mod._FEDisconnected):
                asyncio.run(forwarder({"key": "SPY"}))

        assert any("failed to forward" in r.message for r in caplog.records)

    # ------------------------------------------------------------------
    # Reconnect — success path
    # ------------------------------------------------------------------

    def test_reconnect_sends_reconnecting_and_reconnected_messages(
        self, test_app, monkeypatch
    ):
        """On Schwab stream error the FE should receive status messages."""
        cs._client = MagicMock()
        self._no_sleep(monkeypatch)

        # First client: stream error after login; second: clean disconnect
        broken_sc = MockStreamClient(canned_messages=[RuntimeError("dropped")])
        clean_sc = MockStreamClient(canned_messages=[])
        self._make_clients(monkeypatch, [broken_sc, clean_sc])

        with test_app.websocket_connect("/stream") as ws:
            ws.send_json(self._SUB)
            ack = ws.receive_json()
            assert ack["status"] == "subscribed"

            reconnecting = ws.receive_json()
            assert reconnecting["status"] == "reconnecting"
            assert reconnecting["attempt"] == 1
            assert "max_attempts" in reconnecting
            assert "delay_seconds" in reconnecting

            reconnected = ws.receive_json()
            assert reconnected["status"] == "reconnected"
            assert reconnected["attempt"] == 1

    def test_reconnected_client_is_logged_out_on_fe_disconnect(
        self, test_app, monkeypatch
    ):
        """After a successful reconnect the new StreamClient is cleaned up."""
        cs._client = MagicMock()
        self._no_sleep(monkeypatch)

        broken_sc = MockStreamClient(canned_messages=[RuntimeError("dropped")])
        clean_sc = MockStreamClient(canned_messages=[])
        self._make_clients(monkeypatch, [broken_sc, clean_sc])

        with test_app.websocket_connect("/stream") as ws:
            ws.send_json(self._SUB)
            ws.receive_json()  # subscribed
            ws.receive_json()  # reconnecting
            ws.receive_json()  # reconnected

        assert clean_sc.logout_called

    # ------------------------------------------------------------------
    # Reconnect — exhaustion path
    # ------------------------------------------------------------------

    def test_reconnect_exhaustion_sends_reconnect_failed(
        self, test_app, monkeypatch
    ):
        """After MAX_RECONNECT_ATTEMPTS all fail, FE receives reconnect_failed."""
        cs._client = MagicMock()
        self._no_sleep(monkeypatch)

        # Initial client raises on handle_message; all subsequent clients fail login
        broken_sc = MockStreamClient(canned_messages=[RuntimeError("dropped")])
        failing_sc = MockStreamClient(login_raises=ConnectionError("unreachable"))
        self._make_clients(
            monkeypatch,
            [broken_sc] + [failing_sc] * stream_mod.MAX_RECONNECT_ATTEMPTS,
        )

        with test_app.websocket_connect("/stream") as ws:
            ws.send_json(self._SUB)
            ws.receive_json()  # subscribed

            for attempt in range(1, stream_mod.MAX_RECONNECT_ATTEMPTS + 1):
                msg = ws.receive_json()
                assert msg["status"] == "reconnecting"
                assert msg["attempt"] == attempt

            final = ws.receive_json()
            assert final["error"] == "reconnect_failed"

    def test_reconnect_exhaustion_logs_error(
        self, test_app, monkeypatch, caplog
    ):
        import logging
        cs._client = MagicMock()
        self._no_sleep(monkeypatch)

        broken_sc = MockStreamClient(canned_messages=[RuntimeError("dropped")])
        failing_sc = MockStreamClient(login_raises=ConnectionError("unreachable"))
        self._make_clients(
            monkeypatch,
            [broken_sc] + [failing_sc] * stream_mod.MAX_RECONNECT_ATTEMPTS,
        )

        with caplog.at_level(logging.ERROR, logger="gateway.routers.stream"):
            with test_app.websocket_connect("/stream") as ws:
                ws.send_json(self._SUB)
                # drain all messages
                while True:
                    try:
                        msg = ws.receive_json()
                        if msg.get("error") == "reconnect_failed":
                            break
                    except Exception:
                        break

        assert any("exhausted" in r.message for r in caplog.records)
