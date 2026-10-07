"""Quote endpoint: /quotes."""
import logging
from typing import Any

import schwab
from fastapi import APIRouter, HTTPException, Query

from gateway import client_state

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Market Data"])

_Quote = schwab.client.Client.Quote
_VALID_FIELDS = {f.value for f in _Quote.Fields}


def _require_client():
    try:
        return client_state.get_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/quotes")
def get_quotes(
    symbols: str = Query(..., description="Comma-separated tickers, e.g. SPY,AAPL"),
    fields: str = Query(
        "quote,fundamental,reference",
        description=(
            "Comma-separated field groups to include. "
            "Options: quote, fundamental, extended, reference, regular"
        ),
    ),
) -> dict[str, Any]:
    """Fetch quotes for one or more symbols.

    The ``fundamental`` field group includes P/E ratio, EPS, dividend amount,
    dividend yield, shares outstanding, and average volumes.
    The ``reference`` field group includes ``description`` (company name),
    ``exchange``, ``exchangeName``, ``cusip``, ``isShortable``, and ``htbRate``.
    The ``quote`` field group includes bid, ask, last, volume, OHLC, 52-week
    high/low, mark, net change, and post-market data.

    Note: ``sector`` and ``industry`` are not returned by this endpoint.
    """
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="At least one symbol is required.")

    field_list = [f.strip() for f in fields.split(",") if f.strip()]
    invalid = [f for f in field_list if f not in _VALID_FIELDS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid field(s): {invalid}. Valid options: {sorted(_VALID_FIELDS)}",
        )

    client = _require_client()
    resp = client.get_quotes(symbol_list, fields=[_Quote.Fields(f) for f in field_list])
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"No quotes found for {symbols!r}.")
    try:
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Schwab API error: {resp.status_code}"
        ) from exc

    data = resp.json()
    return {"count": len(data), "data": data}
