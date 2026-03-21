# schwab-gateway

Local HTTP microservice that owns the Schwab OAuth token centrally so every project on your network can call market-data endpoints without holding credentials or managing token files.

```
any notebook / script
        │  HTTP
        ▼
schwab-gateway :8182   ←──── single token.json
        │  schwab-py
        ▼
  Schwab API
```

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service status + token expiry |
| `GET` | `/bars/{symbol}` | Intraday OHLCV (last ~10 days) |
| `GET` | `/daily/{symbol}` | Daily OHLCV (back to ~1985) |
| `GET` | `/weekly/{symbol}` | Weekly OHLCV |
| `GET` | `/reauth` | Generate Schwab authorization URL |
| `POST` | `/reauth/complete` | Exchange code, write token, reinit client |

All responses use the envelope `{"symbol": "SPY", "count": N, "data": [...]}`.

---

## Setup

### Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (`brew install uv`)
- A Schwab developer app — create one at [developer.schwab.com](https://developer.schwab.com)

### Install

```bash
git clone <repo>
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

## First-time token setup (reauth flow)

The service starts without a token — data endpoints return `503` until the flow is complete.

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
curl http://localhost:8182/health
# "age_hours" should be ~0
```

The Postman collection (`schwab-gateway.postman_collection.json`) has a test script on the `/reauth` request that automatically saves the URL to the `auth_url` variable.

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
    "access_expires_at": "2026-03-21T10:30:00"
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
| `clean` | bool | `true` | Filter to RTH 09:30–16:00 ET, parse timestamps |
| `resample_60` | bool | `false` | Resample to hourly bars (implies `clean=true`) |

Schwab has no native 60-min bars — `frequency=60` or `resample_60=true` fetches 30-min internally and resamples.

**`clean=false`** response columns: `ts_ms, open, high, low, close, volume`
**`clean=true`** response columns: `timestamp (ISO), open, high, low, close, volume`

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

### `GET /reauth`

Returns `{"auth_url": "https://api.schwabapi.com/..."}`. Open the URL in a browser.

---

### `POST /reauth/complete`

```json
{ "callback_url": "https://127.0.0.1?code=...&state=..." }
```

Exchanges the code, writes `~/.schwab/token.json`, reinitialises the client.

---

## Token renewal

The Schwab refresh token expires after **7 days**. The background monitor checks every 12 hours:

- When fewer than `ALERT_THRESHOLD_DAYS` (default `2.0`) days remain, it writes `~/.schwab/token_alert.txt`.
- `/health` always shows `refresh_expires_in_hours`.

When the alert fires, repeat the reauth flow above.

---

## Docker (devbox deployment)

```bash
cp .env.example .env  # fill in credentials
docker compose up -d --build
```

The named volume `schwab_token` persists `token.json` across restarts and rebuilds. Complete the reauth flow once after first deploy:

```bash
curl http://devbox.local:8182/reauth
# open URL, complete login, copy callback URL
curl -X POST http://devbox.local:8182/reauth/complete \
  -H "Content-Type: application/json" \
  -d '{"callback_url": "https://127.0.0.1?code=...&state=..."}'
```

See [`deploy/README.md`](deploy/README.md) for full operational notes.

---

## Using from `derivatives-analysis`

Replace direct `schwab-py` calls with the thin gateway client:

```python
# before
from api.schwab.client import get_client, fetch_bars
client = get_client()
df = fetch_bars(client, "SPY", "2026-03-17", "2026-03-21", frequency=5)

# after
from api.schwab.gateway_client import fetch_bars
df = fetch_bars("SPY", "2026-03-17", "2026-03-21", frequency=5)
```

`fetch_daily_bars` and `fetch_weekly_bars` are also available. No `schwab-py` dependency, no local `token.json`.

---

## Project structure

```
gateway/
  main.py              # FastAPI app + lifespan
  settings.py          # pydantic-settings (.env)
  client_state.py      # schwab-py client singleton
  token_utils.py       # pure token age/expiry functions
  routers/
    health.py          # GET /health
    bars.py            # GET /bars, /daily, /weekly
    auth.py            # GET /reauth, POST /reauth/complete
  monitoring/
    token_monitor.py   # asyncio background task
tests/
  conftest.py          # mock helpers (no credentials needed)
  test_bars.py
  test_health.py
  test_token_utils.py
.docker/Dockerfile     # python:3.11-slim + uv
docker-compose.yml     # port 8182, named volume
deploy/README.md       # operational notes
schwab-gateway.postman_collection.json
```
