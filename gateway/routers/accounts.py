"""Account endpoints: /accounts, /accounts/{hash}/orders, /accounts/{hash}/transactions."""
import logging
from datetime import date, datetime, time, timedelta
from typing import Any

import schwab
from fastapi import APIRouter, HTTPException, Query

from gateway import client_state

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Accounts"])

_Order = schwab.client.Client.Order
_Transactions = schwab.client.Client.Transactions
_VALID_STATUSES = {s.value for s in _Order.Status}
_VALID_TYPES = {t.value for t in _Transactions.TransactionType}


def _require_client():
    try:
        return client_state.get_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _json_or_502(resp) -> Any:
    try:
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Schwab API error: {resp.status_code}"
        ) from exc
    return resp.json()


@router.get("/accounts")
def get_accounts() -> dict[str, Any]:
    """List linked accounts as ``{accountNumber, hashValue}``.

    Schwab addresses accounts by ``hashValue``; pass it as ``{account_hash}``
    to the orders and transactions endpoints.
    """
    data = _json_or_502(_require_client().get_account_numbers())
    return {"count": len(data), "data": data}


@router.get("/accounts/{account_hash}/orders")
def get_orders(
    account_hash: str,
    from_date: date | None = Query(None, description="YYYY-MM-DD, default 60 days ago"),
    to_date: date | None = Query(None, description="YYYY-MM-DD, default today"),
    status: str | None = Query(None, description="Order status, e.g. FILLED, WORKING, CANCELED"),
    max_results: int | None = Query(None, ge=1, description="Maximum number of orders"),
) -> dict[str, Any]:
    """Orders entered between ``from_date`` and ``to_date`` (inclusive)."""
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {status!r}. Valid options: {sorted(_VALID_STATUSES)}",
        )

    to_date = to_date or date.today()
    from_date = from_date or to_date - timedelta(days=60)

    resp = _require_client().get_orders_for_account(
        account_hash,
        from_entered_datetime=datetime.combine(from_date, time.min),
        to_entered_datetime=datetime.combine(to_date, time.max),
        status=_Order.Status(status) if status else None,
        max_results=max_results,
    )
    data = _json_or_502(resp)
    return {"count": len(data), "data": data}


@router.get("/accounts/{account_hash}/transactions")
def get_transactions(
    account_hash: str,
    from_date: date | None = Query(None, description="YYYY-MM-DD, default 60 days ago"),
    to_date: date | None = Query(None, description="YYYY-MM-DD, default today"),
    types: str = Query(
        "TRADE",
        description="Comma-separated transaction types, e.g. TRADE,DIVIDEND_OR_INTEREST",
    ),
    symbol: str | None = Query(None, description="Only transactions for this symbol"),
) -> dict[str, Any]:
    """Transactions (fills, dividends, transfers) between the given dates."""
    type_list = [t.strip() for t in types.split(",") if t.strip()]
    invalid = [t for t in type_list if t not in _VALID_TYPES]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type(s): {invalid}. Valid options: {sorted(_VALID_TYPES)}",
        )

    to_date = to_date or date.today()
    from_date = from_date or to_date - timedelta(days=60)

    resp = _require_client().get_transactions(
        account_hash,
        start_date=datetime.combine(from_date, time.min),
        end_date=datetime.combine(to_date, time.max),
        transaction_types=[_Transactions.TransactionType(t) for t in type_list],
        symbol=symbol.upper() if symbol else None,
    )
    data = _json_or_502(resp)
    return {"count": len(data), "data": data}
