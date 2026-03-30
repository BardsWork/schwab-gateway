"""Options chain endpoint: /options/{symbol}."""
import logging
from datetime import date
from typing import Any

import schwab
from fastapi import APIRouter, HTTPException, Query

from gateway import client_state

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Options"])

_Options = schwab.client.Client.Options


# ── helpers ───────────────────────────────────────────────────────────────────

def _require_client():
    try:
        return client_state.get_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _parse_date(s: str) -> date:
    from datetime import datetime
    return datetime.strptime(s, "%Y-%m-%d").date()


def _flatten_exp_date_map(exp_date_map: dict, contract_type: str) -> list[dict]:
    """Flatten a Schwab callExpDateMap or putExpDateMap into a list of contract rows."""
    rows = []
    for exp_key, strikes in exp_date_map.items():
        expiry_str = exp_key.split(":")[0]  # "2024-02-16:20" → "2024-02-16"
        for _strike_key, contracts in strikes.items():
            for contract in contracts:
                bid = float(contract.get("bid") or 0)
                ask = float(contract.get("ask") or 0)
                rows.append({
                    "strike": float(contract.get("strikePrice") or 0),
                    "expiry": expiry_str,
                    "type": contract_type,
                    "bid": bid,
                    "ask": ask,
                    "mid": (bid + ask) / 2.0,
                    "last": float(contract.get("last") or 0),
                    "volume": int(contract.get("totalVolume") or 0),
                    "open_interest": int(contract.get("openInterest") or 0),
                    "iv": float(contract.get("impliedVolatility") or 0),
                    "delta": float(contract.get("delta") or 0),
                    "gamma": float(contract.get("gamma") or 0),
                    "theta": float(contract.get("theta") or 0),
                    "vega": float(contract.get("vega") or 0),
                })
    return rows


def _rescale_iv_if_needed(rows: list[dict]) -> list[dict]:
    """Divide IV by 100 if any value looks like a whole-number percentage (>2.0)."""
    if any(r["iv"] > 2.0 for r in rows):
        for r in rows:
            r["iv"] = r["iv"] / 100.0
    return rows


# ── route ─────────────────────────────────────────────────────────────────────

@router.get("/options/{symbol}")
def get_options(
    symbol: str,
    strike_count: int | None = Query(None, description="Number of strikes around ATM. Omit for all strikes."),
    from_date: str | None = Query(None, description="Filter expirations from this date (YYYY-MM-DD, inclusive)."),
    to_date: str | None = Query(None, description="Filter expirations up to this date (YYYY-MM-DD, inclusive)."),
) -> dict[str, Any]:
    """Fetch a flattened options chain for a symbol.

    Calls the Schwab options chain endpoint and flattens the nested
    ``callExpDateMap`` / ``putExpDateMap`` structure into a plain list of
    contracts, one row per strike × expiry × type combination.

    The response includes:
    - ``underlying_price`` — last trade price of the underlying
    - ``expirations`` — sorted list of unique expiry dates (``YYYY-MM-DD``)
    - ``data`` — flat list of contract objects

    Each contract object has: ``strike``, ``expiry`` (``YYYY-MM-DD``),
    ``type`` (``"call"`` or ``"put"``), ``bid``, ``ask``, ``mid``, ``last``,
    ``volume``, ``open_interest``, ``iv``, ``delta``, ``gamma``, ``theta``,
    ``vega``.

    ``iv`` is Schwab's ``impliedVolatility`` as an annualised decimal
    (e.g. ``0.18`` = 18 %). A defensive check rescales values if Schwab
    returns whole-number percentages (> 2.0) due to entitlement differences.
    """
    client = _require_client()

    kwargs: dict[str, Any] = {
        "symbol": symbol.upper().lstrip("$"),
        "contract_type": _Options.ContractType.ALL,
        "include_underlying_quote": True,
    }
    if strike_count is not None:
        kwargs["strike_count"] = strike_count
    if from_date is not None:
        kwargs["from_date"] = _parse_date(from_date)
    if to_date is not None:
        kwargs["to_date"] = _parse_date(to_date)

    resp = client.get_option_chain(**kwargs)
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"No options data found for {symbol!r}.")
    try:
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Schwab API error: {resp.status_code}"
        ) from exc

    body = resp.json()

    call_map = body.get("callExpDateMap") or {}
    put_map = body.get("putExpDateMap") or {}
    if not call_map and not put_map:
        raise HTTPException(status_code=404, detail=f"No options data found for {symbol!r}.")

    underlying = body.get("underlying") or {}
    underlying_price = float(
        underlying.get("lastPrice") or underlying.get("mark") or 0
    )

    rows = _flatten_exp_date_map(call_map, "call") + _flatten_exp_date_map(put_map, "put")
    rows = _rescale_iv_if_needed(rows)

    # Sort: expiry ascending, then strike ascending, calls before puts
    rows.sort(key=lambda r: (r["expiry"], r["strike"], r["type"]))

    expirations = sorted({r["expiry"] for r in rows})

    return {
        "symbol": symbol.upper().lstrip("$"),
        "underlying_price": underlying_price,
        "expirations": expirations,
        "count": len(rows),
        "data": rows,
    }
