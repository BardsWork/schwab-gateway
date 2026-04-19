"""Market-data routes: /bars, /daily, /weekly."""
import logging
from datetime import datetime, timezone
from typing import Any

import polars as pl
import schwab
from fastapi import APIRouter, HTTPException, Query

from gateway import client_state

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Market Data"])

VALID_FREQUENCIES = [1, 5, 10, 15, 30, 60]

_PH = schwab.client.Client.PriceHistory


# ── helpers ───────────────────────────────────────────────────────────────────

def _require_client():
    try:
        return client_state.get_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _parse_dates(from_date: str, to_date: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(to_date, "%Y-%m-%d").replace(
        hour=23, minute=59, tzinfo=timezone.utc
    )
    return start, end


def _candles_to_raw_df(candles: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "ts_ms": c["datetime"],
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": int(c["volume"]),
            }
            for c in candles
        ]
    )


_RTH_EXPR = (
    (pl.col("timestamp").dt.hour() >= 9)
    & ((pl.col("timestamp").dt.hour() > 9) | (pl.col("timestamp").dt.minute() >= 30))
    & (pl.col("timestamp").dt.hour() < 16)
)


def _ts_ms_to_et(df: pl.DataFrame) -> pl.DataFrame:
    """Convert ts_ms → timestamp (ET timezone), no RTH filter."""
    return (
        df.with_columns(
            pl.from_epoch("ts_ms", time_unit="ms")
            .dt.convert_time_zone("America/New_York")
            .alias("timestamp")
        )
        .drop("ts_ms")
        .sort("timestamp")
    )


def _apply_rth_filter(df: pl.DataFrame) -> pl.DataFrame:
    """Convert ts_ms → timestamp (ET) and keep only RTH bars (09:30–15:59)."""
    return _ts_ms_to_et(df).filter(_RTH_EXPR)


def _resample_to_hourly(df: pl.DataFrame, rth_only: bool = True) -> pl.DataFrame:
    """Resample an intraday DataFrame (timestamp column, ET) to 60-min OHLCV bars."""
    result = (
        df.sort("timestamp")
        .group_by_dynamic("timestamp", every="1h", closed="left")
        .agg(
            [
                pl.col("open").first(),
                pl.col("high").max(),
                pl.col("low").min(),
                pl.col("close").last(),
                pl.col("volume").sum(),
            ]
        )
        .sort("timestamp")
    )
    if rth_only:
        # Drop the pre-9:30 bucket that group_by_dynamic creates at the boundary
        result = result.filter(_RTH_EXPR)
    return result


def _candles_to_daily_df(candles: list[dict]) -> pl.DataFrame:
    return (
        pl.DataFrame(
            [
                {
                    "ts_ms": c["datetime"],
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": int(c["volume"]),
                }
                for c in candles
            ]
        )
        .with_columns(
            pl.from_epoch("ts_ms", time_unit="ms")
            .dt.convert_time_zone("America/New_York")
            .dt.date()
            .alias("date")
        )
        .drop("ts_ms")
        .select(["date", "open", "high", "low", "close", "volume"])
        .sort("date")
    )


def _df_to_response(df: pl.DataFrame, symbol: str) -> dict[str, Any]:
    """Serialise a polars DataFrame to the standard gateway response envelope."""
    # Convert date/datetime columns to strings for JSON serialisation
    serialisable = df
    for col in df.columns:
        dtype = df[col].dtype
        if dtype == pl.Date:
            serialisable = serialisable.with_columns(
                pl.col(col).cast(pl.Utf8)
            )
        elif dtype in (pl.Datetime, pl.Datetime("us", "America/New_York")):
            serialisable = serialisable.with_columns(
                pl.col(col).dt.strftime("%Y-%m-%dT%H:%M:%S%z")
            )
    rows = serialisable.to_dicts()
    return {"symbol": symbol.upper(), "count": len(rows), "data": rows}


def _intraday_freq_enum(minutes: int):
    return {
        1: _PH.Frequency.EVERY_MINUTE,
        5: _PH.Frequency.EVERY_FIVE_MINUTES,
        10: _PH.Frequency.EVERY_TEN_MINUTES,
        15: _PH.Frequency.EVERY_FIFTEEN_MINUTES,
        30: _PH.Frequency.EVERY_THIRTY_MINUTES,
    }[minutes]


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/bars/{symbol}")
def get_bars(
    symbol: str,
    from_date: str = Query(..., description="Start date YYYY-MM-DD (inclusive)"),
    to_date: str = Query(..., description="End date YYYY-MM-DD (inclusive)"),
    frequency: int = Query(5, description="Bar size in minutes: 1/5/10/15/30/60"),
    clean: bool = Query(True, description="Filter to RTH (09:30–16:00 ET) and parse timestamps"),
    resample_60: bool = Query(False, description="Resample to 60-min bars (implies clean=true)"),
) -> dict[str, Any]:
    """Fetch intraday OHLCV bars.

    History limits: ~48 days for 1-min bars, ~9 months for 5-min and higher.
    ``frequency=60`` is accepted but Schwab has no native hourly bars — the
    service fetches 30-min bars and resamples via ``resample_60=true``.
    """
    if frequency not in VALID_FREQUENCIES:
        raise HTTPException(
            status_code=422,
            detail=f"frequency must be one of {VALID_FREQUENCIES}, got {frequency}",
        )
    client = _require_client()

    # Schwab has no native 60-min; frequency=60 implies resample
    effective_resample = resample_60 or (frequency == 60)
    fetch_min = 30 if effective_resample else frequency

    start_dt, end_dt = _parse_dates(from_date, to_date)
    resp = client.get_price_history(
        symbol=symbol.upper(),
        frequency_type=_PH.FrequencyType.MINUTE,
        frequency=_intraday_freq_enum(fetch_min),
        start_datetime=start_dt,
        end_datetime=end_dt,
        need_extended_hours_data=not clean,
    )
    resp.raise_for_status()
    candles = resp.json().get("candles", [])
    if not candles:
        raise HTTPException(
            status_code=404,
            detail=(
            f"No intraday data for {symbol} ({from_date} → {to_date}). "
            f"Schwab history limit: ~48 days for 1-min bars, ~9 months for 5-min and higher."
        ),
        )

    df = _candles_to_raw_df(candles)

    if effective_resample:
        df = _apply_rth_filter(df) if clean else _ts_ms_to_et(df)
        df = _resample_to_hourly(df, rth_only=clean)
    elif clean:
        df = _apply_rth_filter(df)

    return _df_to_response(df, symbol)


@router.get("/daily/{symbol}")
def get_daily(
    symbol: str,
    from_date: str = Query(..., description="Start date YYYY-MM-DD (inclusive)"),
    to_date: str = Query(..., description="End date YYYY-MM-DD (inclusive)"),
) -> dict[str, Any]:
    """Fetch daily OHLCV bars. Schwab history extends back to at least 1985."""
    client = _require_client()
    start_dt, end_dt = _parse_dates(from_date, to_date)

    resp = client.get_price_history_every_day(
        symbol.upper(), start_datetime=start_dt, end_datetime=end_dt,
    )
    resp.raise_for_status()
    candles = resp.json().get("candles", [])
    if not candles:
        raise HTTPException(
            status_code=404,
            detail=f"No daily data for {symbol} ({from_date} → {to_date}).",
        )

    return _df_to_response(_candles_to_daily_df(candles), symbol)


@router.get("/weekly/{symbol}")
def get_weekly(
    symbol: str,
    from_date: str = Query(..., description="Start date YYYY-MM-DD (inclusive)"),
    to_date: str = Query(..., description="End date YYYY-MM-DD (inclusive)"),
) -> dict[str, Any]:
    """Fetch weekly OHLCV bars."""
    client = _require_client()
    start_dt, end_dt = _parse_dates(from_date, to_date)

    resp = client.get_price_history_every_week(
        symbol.upper(), start_datetime=start_dt, end_datetime=end_dt,
    )
    resp.raise_for_status()
    candles = resp.json().get("candles", [])
    if not candles:
        raise HTTPException(
            status_code=404,
            detail=f"No weekly data for {symbol} ({from_date} → {to_date}).",
        )

    return _df_to_response(_candles_to_daily_df(candles), symbol)
