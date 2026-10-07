# schwab-gateway

[![test](https://github.com/BardsWork/schwab-gateway/actions/workflows/test.yml/badge.svg)](https://github.com/BardsWork/schwab-gateway/actions/workflows/test.yml)

A small HTTP service that holds your Schwab OAuth token in one place. Your notebooks, scripts and apps call it over plain HTTP for market data, option chains, quotes, streaming and account history. None of them need `schwab-py`, your app key, or a `token.json`.

```
any notebook / script / app
        │  HTTP or WebSocket
        ▼
schwab-gateway :8182   ←──── one token.json
        │  schwab-py
        ▼
  Schwab API
```

## Why

Schwab's API has two habits that are awkward when several projects share one account:

- **The refresh token expires every 7 days.** Each project that holds its own token needs its own weekly login. The gateway needs one, and it has a browser page for it (`/reauth/ui`).
- **The login redirects to a local callback URL.** Most libraries start a local web server to catch it, which fails when the service runs on a different machine from your browser. The gateway asks you to paste the callback URL instead, so it works on a headless box.

It also flattens option chains into rows, filters intraday bars to regular trading hours, builds 60-minute bars (Schwab has none), and serves its own API reference at `/llm-docs` for AI agents.

Built on [schwab-py](https://github.com/alexgolec/schwab-py), FastAPI and Polars. Read-only: it places no orders.

> [!WARNING]
> **The gateway has no authentication.** Anyone who can reach port 8182 can read your account numbers, orders and transactions, and can start a token renewal. Run it only on a machine and network you trust. Never expose it to the internet. To keep it to one machine, publish the port as `127.0.0.1:8182:8182` in `docker-compose.yml`.

This project is not affiliated with or endorsed by Charles Schwab.

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service status + token expiry |
| `GET` | `/bars/{symbol}` | Intraday OHLCV (~48 days for 1-min; ~9 months for 5-min+) |
| `GET` | `/daily/{symbol}` | Daily OHLCV (back to ~1985) |
| `GET` | `/weekly/{symbol}` | Weekly OHLCV |
| `GET` | `/options/{symbol}` | Flattened options chain with greeks |
| `GET` | `/accounts` | Linked accounts and their hashes |
| `GET` | `/accounts/{account_hash}/orders` | Order history for an account |
| `GET` | `/accounts/{account_hash}/transactions` | Transactions (trades, dividends, transfers) for an account |
| `GET` | `/instruments` | Symbol search and fundamental data |
| `GET` | `/quotes` | Live quotes with bid/ask/last, fundamentals, sector, industry |
| `WS` | `/stream` | Real-time streaming (level 1, charts, books, etc.) |
| `GET` | `/llm-docs` | Machine-readable API reference (for AI agents) |
| `GET` | `/docs` | Swagger UI (interactive API explorer) |
| `GET` | `/openapi.json` | OpenAPI schema |
| `GET` | `/reauth` | Generate Schwab authorization URL |
| `POST` | `/reauth/complete` | Exchange code, write token, reinit client |
| `GET` | `/reauth/status` | Refresh token health (`ok`/`expiring`/`expired`/`missing`) |
| `GET` | `/reauth/ui` | Browser page that walks through renewing the token (no curl needed) |

Data endpoints use the envelope `{"symbol": "SPY", "count": N, "data": [...]}`. The instruments endpoint uses `{"projection": "...", "count": N, "data": {...}}`.

---

## Setup

### Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- A Schwab developer app with the *Accounts and Trading Production* and *Market Data Production* APIs. Create one at [developer.schwab.com](https://developer.schwab.com). Schwab can take a few days to approve it.

### Install

```bash
git clone https://github.com/BardsWork/schwab-gateway.git
cd schwab-gateway
uv sync
```

### Configure

```bash
cp .env.example .env
```

Edit `.env`:

```
SCHWAB_APP_KEY=your_app_key
SCHWAB_API_SECRET=your_api_secret
SCHWAB_CALLBACK_URL=https://127.0.0.1
```

`SCHWAB_CALLBACK_URL` must match the callback URL registered on your Schwab app exactly.

The token is stored at `~/.schwab/token.json` by default. Override with `TOKEN_PATH=`.

---

## Running

### Development

```bash
uv run uvicorn gateway.main:app --reload --port 8182
```

### Tests

```bash
uv run pytest tests/ -v
```

No credentials or network access required — all Schwab calls are mocked.

---

## First-time token setup / renewing an expired token (reauth flow)

The service starts without a token — data endpoints return `503` until the flow is complete. The refresh token expires 7 days after it's issued, so you'll need to repeat this periodically (the token monitor writes a reminder — see below).

### Browser (recommended)

Open **`http://localhost:8182/reauth/ui`**. It shows the current token status, a "Start login" button that opens Schwab's login in a new tab, and a box to paste the callback URL you land on — no curl required.

### curl

```bash
# 1. Get the authorization URL
curl http://localhost:8182/reauth
```

Open the returned `auth_url` in a browser. Log in with MFA as normal. After the redirect, copy the full URL from the address bar (it will look like `https://127.0.0.1?code=...&state=...`).

```bash
# 2. Exchange the code
curl -X POST http://localhost:8182/reauth/complete \
  -H "Content-Type: application/json" \
  -d '{"callback_url": "https://127.0.0.1?code=YOUR_CODE&state=YOUR_STATE"}'

# 3. Verify
curl http://localhost:8182/reauth/status
# {"status": "ok", "remaining_days": 7.0}
```

The Swagger UI at `/docs` lists all endpoints and lets you execute requests directly in the browser.

---

## API reference

### `GET /health`

```json
{
  "status": "ok",
  "client_ready": true,
  "token": {
    "age_hours": 1.5,
    "refresh_expires_in_hours": 166.5,
    "refresh_expires_at": "2026-04-05T10:30:00+00:00"
  }
}
```

`status` is `"degraded"` when the client is not initialised.

---

### `GET /bars/{symbol}`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `from_date` | string | required | `YYYY-MM-DD` start (inclusive) |
| `to_date` | string | required | `YYYY-MM-DD` end (inclusive) |
| `frequency` | int | `5` | Bar size: `1 \| 5 \| 10 \| 15 \| 30 \| 60` minutes |
| `clean` | bool | `true` | `true`: RTH only (09:30–15:59 ET), timestamps as ISO strings. `false`: full session including pre/after-market. |
| `resample_60` | bool | `false` | Resample to hourly bars. RTH filtering controlled independently by `clean`. |

Schwab has no native 60-min bars — `frequency=60` or `resample_60=true` fetches 30-min internally and resamples. History limits: ~48 days for `frequency=1`; ~9 months for `frequency=5` and higher.

**Response time column:** `timestamp` (ISO 8601 with ET offset) when `clean=true` or when resampling (`frequency=60`/`resample_60=true`); `ts_ms` (epoch ms, UTC) when `clean=false` and no resampling.

```bash
curl "http://localhost:8182/bars/SPY?from_date=2026-03-17&to_date=2026-03-21&frequency=5"
```

---

### `GET /daily/{symbol}`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `from_date` | string | required | `YYYY-MM-DD` |
| `to_date` | string | required | `YYYY-MM-DD` |

Columns: `date, open, high, low, close, volume`

```bash
curl "http://localhost:8182/daily/SPY?from_date=2025-01-01&to_date=2026-03-21"
```

---

### `GET /weekly/{symbol}`

Same parameters as `/daily`. Each bar represents one calendar week.

---

### `GET /options/{symbol}`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `strike_count` | int | `null` | Number of strikes centred around ATM. Omit for all strikes. |
| `from_date` | string | `null` | Filter expirations from this date (`YYYY-MM-DD`, inclusive). |
| `to_date` | string | `null` | Filter expirations up to this date (`YYYY-MM-DD`, inclusive). |

Returns a flattened options chain. The `$` prefix is stripped from symbols (e.g. `$SPY` → `SPY`).

**Response envelope:** `{"symbol": "SPY", "underlying_price": 500.0, "expirations": ["2024-02-16", ...], "count": N, "data": [...]}`

Each contract row: `strike`, `expiry` (YYYY-MM-DD), `type` (`"call"` or `"put"`), `bid`, `ask`, `mid`, `last`, `volume`, `open_interest`, `iv` (annualised decimal, e.g. `0.18` = 18%), `delta`, `gamma`, `theta`, `vega`.

`underlying_price` uses `lastPrice`, falling back to `mark` if zero. Missing/null greeks default to `0.0`. A defensive rescale divides `iv` by 100 if Schwab returns whole-number percentages (> 2.0).

```bash
curl "http://localhost:8182/options/SPY"
curl "http://localhost:8182/options/SPY?strike_count=10&from_date=2024-02-01&to_date=2024-02-28"
```

---

### `GET /accounts`

Lists linked accounts as `{"count": N, "data": [{"accountNumber": "...", "hashValue": "..."}]}`. Schwab addresses accounts by `hashValue`; use it as `{account_hash}` below.

---

### `GET /accounts/{account_hash}/orders`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `from_date` | string | 60 days ago | `YYYY-MM-DD` start (inclusive) |
| `to_date` | string | today | `YYYY-MM-DD` end (inclusive) |
| `status` | string | all | One order status, e.g. `FILLED`, `WORKING`, `CANCELED` |
| `max_results` | int | Schwab default | Maximum number of orders |

```bash
curl "http://localhost:8182/accounts/$HASH/orders?status=FILLED"
```

Response: `{"count": N, "data": [...]}` — Schwab's order objects, unmodified.

---

### `GET /accounts/{account_hash}/transactions`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `from_date` | string | 60 days ago | `YYYY-MM-DD` |
| `to_date` | string | today | `YYYY-MM-DD` |
| `types` | string | `TRADE` | Comma-separated, e.g. `TRADE,DIVIDEND_OR_INTEREST` |
| `symbol` | string | all | Only transactions for this symbol |

```bash
curl "http://localhost:8182/accounts/$HASH/transactions?symbol=SPY"
```

Response: `{"count": N, "data": [...]}` — Schwab's transaction objects, unmodified.

---

### `GET /instruments`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `symbols` | string | required | Comma-separated tickers or a search term, e.g. `SPY,AAPL` |
| `projection` | string | `fundamental` | `fundamental` \| `symbol-search` \| `symbol-regex` \| `desc-search` \| `desc-regex` \| `search` |

Returns fundamental metrics for `fundamental` projection, or matching instrument records for search projections. For `fundamental`, response is `{"data": {"instruments": [{"cusip": ..., "symbol": ..., "description": "APPLE INC", "exchange": ..., "assetType": ..., "fundamental": {...}}]}}`. Note: `sector` and `industry` are not returned.

```bash
curl "http://localhost:8182/instruments?symbols=SPY&projection=fundamental"
curl "http://localhost:8182/instruments?symbols=SP&projection=symbol-search"
```

Response: `{"projection": "fundamental", "count": 1, "data": {"instruments": [...]}}`

---

### `GET /quotes`

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `symbols` | string | required | Comma-separated tickers, e.g. `SPY,AAPL` |
| `fields` | string | `quote,fundamental,reference` | Comma-separated groups: `quote`, `fundamental`, `extended`, `reference`, `regular` |

The `fundamental` group includes P/E ratio, EPS, dividend amount/yield/dates, shares outstanding, and average volumes. The `reference` group includes `description` (company name), `exchange`, `exchangeName`, `cusip`, `isShortable`, `htbRate`. The `quote` group includes bid, ask, last, volume, OHLC, 52-week high/low, mark, net change, and post-market data. Note: `sector` and `industry` are **not** returned by this endpoint.

```bash
curl "http://localhost:8182/quotes?symbols=SPY,AAPL"
curl "http://localhost:8182/quotes?symbols=SPY&fields=fundamental,reference"
```

Response: `{"count": 1, "data": {"SPY": {"quote": {...}, "fundamental": {...}, "reference": {...}}}}`

---

### `WS /stream`

Real-time market data via the Schwab StreamClient.

**Protocol:**
1. Connect to `ws://localhost:8182/stream`
2. Send a JSON subscription spec (within 10 s)
3. Receive `{"status": "subscribed", "count": N}` on success
4. Receive stream messages as `{"service": "<SERVICE>", "content": {...}}`
5. Close when done — the gateway tears down the upstream WebSocket

**Subscription spec:**
```json
{
  "subscriptions": [
    {"type": "level_one_equity", "symbols": ["SPY", "QQQ"]},
    {"type": "chart_equity",     "symbols": ["SPY"]},
    {"type": "account_activity"}
  ]
}
```

**Subscription types:** `level_one_equity`, `chart_equity`, `level_one_option`, `level_one_futures`, `chart_futures`, `level_one_forex`, `level_one_futures_options`, `nyse_book`, `nasdaq_book`, `options_book`, `screener_equity`, `screener_option`, `account_activity`

`account_activity` requires no `symbols` field. All other types require a non-empty `symbols` list.

Each `/stream` connection opens its own Schwab streamer session. Schwab limits how many sessions one account can hold, so a second client can interrupt the first. The gateway then reconnects the interrupted one (up to 3 attempts) and sends `{"status": "reconnecting"}` / `{"status": "reconnected"}` messages while it does.

---

### `GET /llm-docs`

Returns the full API reference as `text/plain` (Markdown). Designed for AI agent consumption — an agent in another repo can call this endpoint to understand every endpoint, parameter, response shape, and the streaming protocol without exploring the source.

```bash
curl "http://localhost:8182/llm-docs"
```

---

### `GET /reauth`

Returns `{"auth_url": "https://api.schwabapi.com/..."}`. Open the URL in a browser.

---

### `POST /reauth/complete`

```json
{ "callback_url": "https://127.0.0.1?code=...&state=..." }
```

Exchanges the code, writes `~/.schwab/token.json`, reinitialises the client.

---

### `GET /reauth/status`

```json
{ "status": "ok", "remaining_days": 6.4 }
```

`status` is one of `ok` / `expiring` / `expired` / `missing`. `remaining_days` is `null` when `status` is `missing`.

---

### `GET /reauth/ui`

Returns an HTML page (not JSON) that wraps `/reauth`, `/reauth/complete`, and `/reauth/status` into a single click-through flow — open it in a browser to renew without curl.

---

## Token renewal

The Schwab refresh token expires after **7 days**. The background monitor checks every 12 hours:

- When fewer than `ALERT_THRESHOLD_DAYS` (default `2.0`) days remain, it writes `~/.schwab/token_alert.txt`.
- `/health` always shows `refresh_expires_in_hours`; `/reauth/status` gives the same thing pre-digested for scripts.

When the alert fires, open `/reauth/ui` in a browser and follow the two steps, or repeat the curl flow above.

---

## Docker

```bash
cp .env.example .env  # fill in credentials
docker network create schwab-net
docker compose up -d --build
```

Then open `http://localhost:8182/reauth/ui` and complete the login once.

- The named volume `schwab_token` keeps `token.json` across restarts and rebuilds.
- `schwab-net` is an external Docker network. Other compose stacks join it to reach the gateway at `http://schwab-gateway:8182` without going through the host.
- The compose file mounts `./gateway` and runs uvicorn with `--reload`, so code edits apply without a rebuild.

See [`deploy/README.md`](deploy/README.md) for operational notes.

---

## Calling it from your code

Any HTTP client works. With Polars:

```python
import httpx
import polars as pl

resp = httpx.get(
    "http://localhost:8182/bars/SPY",
    params={"from_date": "2026-03-17", "to_date": "2026-03-21", "frequency": 5},
)
resp.raise_for_status()
df = pl.DataFrame(resp.json()["data"])
```

Errors use FastAPI's `{"detail": "..."}` body. `400` means a bad parameter, `503` means no valid token yet (complete the reauth flow), and `502` means Schwab returned an error.

---

## Project structure

```
gateway/
  main.py              # FastAPI app + lifespan
  settings.py          # pydantic-settings (.env)
  client_state.py      # schwab-py client singleton
  token_utils.py       # token age/expiry functions, token file writer
  routers/
    health.py          # GET /health
    bars.py            # GET /bars, /daily, /weekly
    options.py         # GET /options
    accounts.py        # GET /accounts, /accounts/{hash}/orders, /accounts/{hash}/transactions
    instruments.py     # GET /instruments
    quotes.py          # GET /quotes
    stream.py          # WS /stream
    auth.py            # GET /reauth, /reauth/status, /reauth/ui, POST /reauth/complete
    llm_docs.py        # GET /llm-docs
  llm_docs.md          # machine-readable API reference (served by llm_docs.py)
  monitoring/
    token_monitor.py   # asyncio background task
tests/                 # one file per router; conftest.py holds the mocks
.docker/Dockerfile     # python:3.11-slim + uv
docker-compose.yml     # port 8182, named token volume
deploy/README.md       # operational notes
```

---

## License

[MIT](LICENSE)
