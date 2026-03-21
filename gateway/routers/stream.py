"""WebSocket streaming endpoint: WS /stream."""
import asyncio
import logging
from typing import Any

import schwab
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from gateway import client_state

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Streaming"])

# Maps user-facing subscription type → (subs_method, handler_method, service_key)
_SUBSCRIPTION_MAP: dict[str, tuple[str, str, str]] = {
    "level_one_equity":           ("level_one_equity_subs",           "add_level_one_equity_handler",           "LEVELONE_EQUITIES"),
    "chart_equity":               ("chart_equity_subs",               "add_chart_equity_handler",               "CHART_EQUITY"),
    "level_one_option":           ("level_one_option_subs",           "add_level_one_option_handler",           "LEVELONE_OPTIONS"),
    "level_one_futures":          ("level_one_futures_subs",          "add_level_one_futures_handler",          "LEVELONE_FUTURES"),
    "chart_futures":              ("chart_futures_subs",              "add_chart_futures_handler",              "CHART_FUTURES"),
    "level_one_forex":            ("level_one_forex_subs",            "add_level_one_forex_handler",            "LEVELONE_FOREX"),
    "level_one_futures_options":  ("level_one_futures_options_subs",  "add_level_one_futures_options_handler",  "LEVELONE_FUTURES_OPTIONS"),
    "nyse_book":                  ("nyse_book_subs",                  "add_nyse_book_handler",                  "NYSE_BOOK"),
    "nasdaq_book":                ("nasdaq_book_subs",                "add_nasdaq_book_handler",                "NASDAQ_BOOK"),
    "options_book":               ("options_book_subs",               "add_options_book_handler",               "OPTIONS_BOOK"),
    "screener_equity":            ("screener_equity_subs",            "add_screener_equity_handler",            "SCREENER_EQUITY"),
    "screener_option":            ("screener_option_subs",            "add_screener_option_handler",            "SCREENER_OPTION"),
    "account_activity":           ("account_activity_sub",            "add_account_activity_handler",           "ACCT_ACTIVITY"),
}


def _make_forwarder(ws: WebSocket, service: str):
    """Return an async handler that forwards labeled messages to the WebSocket."""
    async def _forward(msg: dict) -> None:
        try:
            await ws.send_json({"service": service, "content": msg})
        except Exception:
            pass  # WebSocket already closing
    return _forward


def _parse_subscriptions(raw: Any) -> list[dict]:
    """Validate the subscription spec; raise ValueError with a clear message on error."""
    if not isinstance(raw, dict) or "subscriptions" not in raw:
        raise ValueError("Message must be a JSON object with a 'subscriptions' key.")
    specs = raw["subscriptions"]
    if not isinstance(specs, list) or len(specs) == 0:
        raise ValueError("'subscriptions' must be a non-empty list.")
    for spec in specs:
        sub_type = spec.get("type")
        if sub_type not in _SUBSCRIPTION_MAP:
            raise ValueError(
                f"Unknown subscription type: {sub_type!r}. "
                f"Valid types: {sorted(_SUBSCRIPTION_MAP)}"
            )
        if sub_type != "account_activity" and not spec.get("symbols"):
            raise ValueError(
                f"Subscription type {sub_type!r} requires a non-empty 'symbols' list."
            )
    return specs


@router.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    """Real-time streaming via Schwab StreamClient.

    **Protocol:**
    1. Connect.
    2. Send a JSON subscription spec as the first message (10-second timeout).
    3. Receive ``{"status": "subscribed", "count": N}`` on success.
    4. Receive stream messages as ``{"service": "<SERVICE>", "content": {...}}``.
    5. Close the connection when done — the gateway tears down the StreamClient.

    **Subscription spec example:**

        {
            "subscriptions": [
                {"type": "level_one_equity", "symbols": ["SPY", "QQQ"]},
                {"type": "chart_equity",     "symbols": ["SPY"]},
                {"type": "account_activity"}
            ]
        }

    **Subscription types:** level_one_equity, chart_equity, level_one_option,
    level_one_futures, chart_futures, level_one_forex, level_one_futures_options,
    nyse_book, nasdaq_book, options_book, screener_equity, screener_option,
    account_activity.
    """
    await ws.accept()

    # 503 guard — must have an initialised HTTP client to build a StreamClient
    try:
        http_client = client_state.get_client()
    except RuntimeError as exc:
        await ws.send_json({"error": "no_client", "detail": str(exc)})
        await ws.close(code=1011)
        return

    # Read and validate the subscription spec (first message)
    try:
        raw = await asyncio.wait_for(ws.receive_json(), timeout=10.0)
        specs = _parse_subscriptions(raw)
    except asyncio.TimeoutError:
        await ws.send_json({
            "error": "invalid_spec",
            "detail": "Timed out waiting for subscription message (10 s).",
        })
        await ws.close(code=1002)
        return
    except ValueError as exc:
        await ws.send_json({"error": "invalid_spec", "detail": str(exc)})
        await ws.close(code=1002)
        return

    # Build StreamClient, log in, subscribe
    stream_client = schwab.streaming.StreamClient(http_client)
    try:
        await stream_client.login()

        for spec in specs:
            sub_type = spec["type"]
            subs_method_name, handler_method_name, service_key = _SUBSCRIPTION_MAP[sub_type]

            # Register forwarding handler before subscribing
            getattr(stream_client, handler_method_name)(
                _make_forwarder(ws, service_key)
            )

            # Subscribe (account_activity takes no symbols)
            subs_method = getattr(stream_client, subs_method_name)
            if sub_type == "account_activity":
                await subs_method()
            else:
                await subs_method(spec["symbols"])

        await ws.send_json({"status": "subscribed", "count": len(specs)})
        logger.info("Stream WS: subscribed to %d feed(s).", len(specs))

        # Message pump — runs until the client disconnects or an error occurs
        while True:
            await stream_client.handle_message()

    except WebSocketDisconnect:
        logger.info("Stream WS: client disconnected.")
    except Exception as exc:
        logger.exception("Stream WS error: %s", exc)
        try:
            await ws.send_json({"error": "stream_error", "detail": str(exc)})
        except Exception:
            pass
    finally:
        try:
            await stream_client.logout()
        except Exception:
            pass
