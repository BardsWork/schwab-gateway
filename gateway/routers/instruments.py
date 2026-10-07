"""Instrument search and fundamental data: /instruments."""
import logging
from typing import Any

import schwab
from fastapi import APIRouter, HTTPException, Query

from gateway import client_state

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Market Data"])

_Projection = schwab.client.Client.Instrument.Projection
_VALID_PROJECTIONS = {p.value for p in _Projection}


def _require_client():
    try:
        return client_state.get_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/instruments")
def get_instruments(
    symbols: str = Query(..., description="Symbol(s), comma-separated (e.g. SPY,AAPL)"),
    projection: str = Query(
        "fundamental",
        description=(
            "Search mode. One of: symbol-search, symbol-regex, "
            "desc-search, desc-regex, search, fundamental"
        ),
    ),
) -> dict[str, Any]:
    """Search for instruments or retrieve fundamental data.

    For ``fundamental`` and ``symbol-search``, ``symbols`` may be a
    comma-separated list of exact tickers.  For regex/description projections,
    pass a single search term.

    For ``fundamental`` projection the response shape is
    ``{"projection": "fundamental", "count": N, "data": {"instruments": [...]}}``
    where each element has top-level fields ``cusip``, ``symbol``, ``description``
    (company name), ``exchange``, ``assetType``, and a nested ``fundamental``
    object with P/E, EPS, margins, beta, market cap, dividend data, etc.

    Note: ``sector`` and ``industry`` are not returned by this endpoint.
    """
    if projection not in _VALID_PROJECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"projection must be one of {sorted(_VALID_PROJECTIONS)}, got {projection!r}",
        )

    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="At least one symbol is required.")

    client = _require_client()

    resp = client.get_instruments(symbol_list, _Projection(projection))
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"No instruments found for {symbols!r}.")
    try:
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Schwab API error: {resp.status_code}"
        ) from exc

    data = resp.json()
    return {"projection": projection, "count": len(data), "data": data}
