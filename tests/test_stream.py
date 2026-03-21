"""Tests for WS /stream."""
from unittest.mock import MagicMock

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
