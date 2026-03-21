# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A single-purpose local HTTP microservice that owns the Schwab OAuth token and proxies market-data calls. Every project on the local network hits this service over HTTP — no `schwab-py` dependency, no local `token.json` in consuming projects.

Port **8182**. Deployed via Docker Compose on a devbox at `192.168.1.177`.

---

## Commands

```bash
# Install / sync deps (always use uv — never pip)
uv sync

# Run locally
uv run uvicorn gateway.main:app --reload --port 8182

# Tests (no credentials or network needed — all mocked)
uv run pytest tests/ -v

# Docker
docker compose up -d --build
docker compose logs -f
docker compose down
```

---

## Architecture

```
.docker/
  Dockerfile         # python:3.11-slim + uv; build context is repo root
gateway/
  main.py            # FastAPI app + asynccontextmanager lifespan
  settings.py        # pydantic-settings singleton (get_settings())
  client_state.py    # schwab-py client module-level singleton
  token_utils.py     # pure functions — no I/O side effects except write_token()
  routers/
    health.py        # GET /health
    bars.py          # GET /bars/{symbol}, /daily/{symbol}, /weekly/{symbol}
    auth.py          # GET /reauth, POST /reauth/complete
  monitoring/
    token_monitor.py # asyncio background task, runs every 12h
tests/
  conftest.py        # all mock helpers live here
```

## Settings singleton

`get_settings()` in `gateway/settings.py` caches a `Settings` instance in `_settings`. In tests, patch it with `monkeypatch.setattr("gateway.settings._settings", Settings(...))` — see `tests/conftest.py`.

---

## Critical schwab-py behaviour

### Frequency enums only cover intraday

```python
# schwab.client.Client.PriceHistory.Frequency only has:
# EVERY_MINUTE, EVERY_FIVE_MINUTES, EVERY_TEN_MINUTES,
# EVERY_FIFTEEN_MINUTES, EVERY_THIRTY_MINUTES
#
# There is NO EVERY_DAY, EVERY_WEEK, EVERY_MONTH.
```

Daily and weekly bars use **helper methods**, not `get_price_history` with a frequency enum:

```python
client.get_price_history_every_day(symbol, start_datetime=..., end_datetime=...)
client.get_price_history_every_week(symbol, start_datetime=..., end_datetime=...)
```

If you add a monthly endpoint, use `get_price_history_every_month()`.

### 60-min bars do not exist natively

`frequency=60` fetches 30-min bars and resamples. `resample_60=true` on `/bars` does the same. Both paths set `fetch_min = 30` and call `_resample_to_hourly()` after `_apply_rth_filter()`.

### `client_from_token_file` not `easy_client`

`init_client()` uses `schwab.auth.client_from_token_file(token_path, api_key, app_secret)`. This never opens a browser. `easy_client` is banned here — it tries to start a local Flask server to catch the OAuth callback, which breaks when the service runs on a different machine than the user's browser.

---

## Token file format

Located at `~/.schwab/token.json` (inside the Docker volume `schwab_token`).

```json
{
  "creation_timestamp": 1773937516,
  "token": {
    "expires_in": 1800,
    "token_type": "Bearer",
    "scope": "api",
    "refresh_token": "...",
    "access_token": "...",
    "id_token": "...",
    "expires_at": 1774010584
  }
}
```

- `creation_timestamp` — Unix epoch when the token was first obtained. The 7-day refresh window is measured from here.
- `expires_at` — Unix epoch for access token expiry (30 min after issue).
- `write_token()` in `token_utils.py` computes both fields and writes this structure after a successful OAuth exchange.

---

## Reauth flow

Why not schwab-py's built-in `client_from_login_flow`: it starts a Flask server on `127.0.0.1` to catch the callback — breaks when the browser is on a different machine than the service.

Instead:

1. `GET /reauth` — builds the OAuth URL with `urllib.parse.urlencode`, stores state in `_pending_state` (module-level in `auth.py`), returns `{"auth_url": "..."}`.
2. User opens URL in browser, completes MFA, copies the callback URL.
3. `POST /reauth/complete {"callback_url": "..."}` — verifies state, exchanges code via `httpx.post` to `https://api.schwabapi.com/v1/oauth/token` with Basic auth, calls `write_token()`, then `client_state.reset_client()`.

The Schwab token endpoint uses HTTP Basic auth: `base64(app_key:api_secret)`.

---

## Startup behaviour

`init_client()` is called in the FastAPI lifespan. If `token.json` is missing it **logs a warning and continues** — it does not crash the process. Data routes return `503`; reauth routes remain accessible. This allows completing the reauth flow on a fresh deploy before any data calls.

---

## Test patterns

All tests mock at the `client_state._client` level — no real schwab auth is ever attempted.

```python
# In a test method — replace _client with a mock that returns canned candles:
import gateway.client_state as cs
cs._client = mock_schwab_client([make_candle(JAN2_0930_ET_MS)])

# To test the 503 path:
monkeypatch.setattr(cs, "_client", None)
```

The `test_app` fixture in `conftest.py` patches both `gateway.settings._settings` (to point at a tmp token file) and `gateway.client_state._client` (to a generic `MagicMock()`). Use it for any test that needs a running app.

`mock_schwab_client()` in `conftest.py` stubs all three history methods:
- `get_price_history` (intraday)
- `get_price_history_every_day` (daily)
- `get_price_history_every_week` (weekly)

All return the same canned candle list. If you add monthly, stub `get_price_history_every_month` there too.

### Reference timestamps (epoch-ms, ET)

```python
JAN2_0930_ET_MS  = 1704205800000  # 2024-01-02 09:30 ET  ← use for intraday
JAN2_MIDNIGHT_ET_MS = 1704153600000  # 2024-01-02 00:00 ET  ← use for daily
FEB1_MIDNIGHT_ET_MS = 1706745600000  # 2024-02-01 00:00 ET
```

---

## RTH filter logic

Intraday bars are filtered to regular trading hours (09:30–15:59 ET) in `_apply_rth_filter()`. The condition is:

```python
(hour >= 9) & ((hour > 9) | (minute >= 30)) & (hour < 16)
```

This is identical to the logic in `derivatives-analysis/api/schwab/client.py`. Keep them in sync if either changes.

---

## Response envelope

All data routes return:

```json
{"symbol": "SPY", "count": N, "data": [...]}
```

`_df_to_response()` in `bars.py` handles serialisation. `pl.Date` columns are cast to `Utf8` (→ `"2024-01-02"`). `pl.Datetime` columns use `.dt.strftime("%Y-%m-%dT%H:%M:%S%z")`. Never return polars objects directly.

---

## Adding a new endpoint

1. Add the route function to the appropriate router file (or create a new one in `gateway/routers/`).
2. Register the router in `gateway/main.py` with `app.include_router(...)`.
3. Add tests — stub `cs._client = mock_schwab_client([...])` in the test body.
4. Add the request to `schwab-gateway.postman_collection.json` with an example response.
5. Update the API reference table in `README.md`.

---

## Dependency management

`uv` only — `.venv/` is local, nothing is installed globally. `pyproject.toml` is the single source of truth; `requirements.txt` does not exist. `.docker/Dockerfile` uses the official `uv` image layer and `uv sync --no-dev --frozen`.

To add a dependency:
```bash
uv add <package>          # runtime
uv add --dev <package>    # test/dev only
```

---

## Consuming service (derivatives-analysis)

`api/schwab/gateway_client.py` is the thin wrapper. It targets `http://192.168.1.177:8182`. The three public functions mirror the old `client.py` signatures minus the `client` argument:

```python
fetch_bars(symbol, from_date, to_date, frequency=5, clean=True, resample_60=False) -> pl.DataFrame
fetch_daily_bars(symbol, from_date, to_date) -> pl.DataFrame
fetch_weekly_bars(symbol, from_date, to_date) -> pl.DataFrame
```

Notebooks that previously called `get_client()` + `fetch_bars(client, ...)` should be migrated to these. The old `api/schwab/client.py` still exists and still works for direct local use — it just requires a local `token.json`.
