# schwab-gateway API Reference

**Service:** Local HTTP proxy for Schwab market-data and streaming.
**Base URL:** `http://localhost:8182`
**Purpose:** Centralises Schwab OAuth token management. Consuming projects call this service — no `schwab-py` dependency, no local `token.json` needed.

---

## Common patterns

- Data endpoints return `503` when the Schwab client is not initialised (token missing — run the reauth flow).
- Standard REST envelope: `{"symbol": "SPY", "count": N, "data": [...]}`.
- Instruments envelope: `{"projection": "fundamental", "count": N, "data": {...}}`.
- Error envelope: FastAPI default `{"detail": "..."}`.

---

## REST Endpoints

### GET /health

Returns service and token status. No parameters.

```json
{
  "status": "ok",
  "client_ready": true,
  "token": {
    "age_hours": 1.5,
    "refresh_expires_in_hours": 166.5,
    "refresh_expires_at": "2026-03-28T10:30:00"
  }
}
```

`status` is `"degraded"` when `client_ready` is false.

When the token file is missing or unreadable, `token` contains `{"error": "token file not found"}` or `{"error": "<exception message>"}` instead of the usual fields.

---

### GET /bars/{symbol}

Intraday OHLCV bars. History limits: ~48 days for 1-min bars; ~9 months for 5-min and higher frequencies.

| Param | Type | Default | Description |
|---|---|---|---|
| `from_date` | string | required | `YYYY-MM-DD` start (inclusive) |
| `to_date` | string | required | `YYYY-MM-DD` end (inclusive) |
| `frequency` | int | `5` | Bar size in minutes: `1`, `5`, `10`, `15`, `30`, `60` |
| `clean` | bool | `true` | `true`: RTH only (09:30–15:59 ET), timestamps as ISO strings. `false`: full session including pre/after-market (requests extended hours from Schwab). |
| `resample_60` | bool | `false` | Resample to hourly bars. RTH filtering controlled independently by `clean`. |

`frequency=60` fetches 30-min bars internally and resamples — Schwab has no native 60-min bars.

**Response column by mode:**

| `clean` | `frequency` / `resample_60` | Time column | Session |
|---|---|---|---|
| `true` | any | `timestamp` (ISO 8601 with ET offset) | RTH only |
| `false` | `< 60`, no resample | `ts_ms` (epoch milliseconds, UTC) | Full session |
| `false` | `60` or `resample_60=true` | `timestamp` (ISO 8601 with ET offset) | Full session |

```
GET /bars/SPY?from_date=2026-03-17&to_date=2026-03-21&frequency=5
→ {"symbol":"SPY","count":390,"data":[{"timestamp":"2026-03-17T09:30:00-04:00","open":591.0,...},...]}

GET /bars/SPY?from_date=2026-03-17&to_date=2026-03-21&frequency=5&clean=false
→ {"symbol":"SPY","count":780,"data":[{"ts_ms":1742204400000,"open":589.5,...},...]}
```

---

### GET /daily/{symbol}

Daily OHLCV bars. History back to ~1985.

| Param | Type | Default | Description |
|---|---|---|---|
| `from_date` | string | required | `YYYY-MM-DD` |
| `to_date` | string | required | `YYYY-MM-DD` |

**Response columns:** `date` (YYYY-MM-DD), `open`, `high`, `low`, `close`, `volume`

```
GET /daily/SPY?from_date=2025-01-01&to_date=2026-03-21
→ {"symbol":"SPY","count":298,"data":[{"date":"2025-01-02","open":590.0,...},...]}
```

---

### GET /weekly/{symbol}

Weekly OHLCV bars. Same parameters and column format as `/daily`.

---

### GET /options/{symbol}

Fetch a flattened options chain for a symbol. Returns all calls and puts across all expirations (or a filtered subset), plus the underlying spot price and a sorted list of expiry dates.

| Param | Type | Default | Description |
|---|---|---|---|
| `strike_count` | int | `null` | Number of strikes centred around ATM. Omit for all strikes. |
| `from_date` | string | `null` | Filter expirations from this date (`YYYY-MM-DD`, inclusive). |
| `to_date` | string | `null` | Filter expirations up to this date (`YYYY-MM-DD`, inclusive). |

**Response fields:**
- `symbol` — normalised symbol (upper-case, `$` stripped)
- `underlying_price` — last trade price of the underlying (falls back to `mark` if `lastPrice` is zero)
- `expirations` — sorted list of unique expiry dates (`YYYY-MM-DD` strings)
- `count` — total number of contract rows
- `data` — flat list of contracts, sorted by expiry, then strike, then type (calls before puts)

**Each contract object:**

| Field | Type | Notes |
|---|---|---|
| `strike` | float | Strike price |
| `expiry` | string | `YYYY-MM-DD` |
| `type` | string | `"call"` or `"put"` |
| `bid` | float | |
| `ask` | float | |
| `mid` | float | `(bid + ask) / 2` |
| `last` | float | Last traded price |
| `volume` | int | |
| `open_interest` | int | |
| `iv` | float | Annualised implied volatility as a decimal (e.g. `0.18` = 18 %) |
| `delta` | float | |
| `gamma` | float | |
| `theta` | float | |
| `vega` | float | |

Missing or null greeks default to `0.0`. A defensive check rescales `iv` values if Schwab returns whole-number percentages (> 2.0) due to entitlement differences.

```
GET /options/SPY
→ {
    "symbol": "SPY",
    "underlying_price": 500.0,
    "expirations": ["2024-02-16", "2024-03-15"],
    "count": 240,
    "data": [
      {"strike": 490.0, "expiry": "2024-02-16", "type": "call",
       "bid": 11.0, "ask": 11.10, "mid": 11.05, "last": 11.0,
       "volume": 5000, "open_interest": 20000,
       "iv": 0.18, "delta": 0.62, "gamma": 0.02, "theta": -0.08, "vega": 0.25},
      ...
    ]
  }

GET /options/SPY?strike_count=10&from_date=2024-02-01&to_date=2024-02-28
→ filtered chain, ±5 strikes around ATM, Feb expirations only
```

---

### GET /quotes

Fetch live quotes for one or more symbols. Returns bid/ask/last prices, fundamentals (sector, industry, company name, P/E, EPS, dividend yield), and reference data.

| Param | Type | Default | Description |
|---|---|---|---|
| `symbols` | string | required | Comma-separated tickers, e.g. `SPY,AAPL` |
| `fields` | string | `quote,fundamental,reference` | Comma-separated groups: `quote`, `fundamental`, `extended`, `reference`, `regular` |

**Field groups:**
- `quote` — 52WeekHigh, 52WeekLow, askMICId, askPrice, askSize, askTime, bidMICId, bidPrice, bidSize, bidTime, closePrice, highPrice, lastMICId, lastPrice, lastSize, lowPrice, mark, markChange, markPercentChange, netChange, netPercentChange, openPrice, postMarketChange, postMarketPercentChange, quoteTime, securityStatus, totalVolume, tradeTime
- `fundamental` — avg10DaysVolume, avg1YearVolume, declarationDate, divAmount, divExDate, divFreq, divPayAmount, divPayDate, divYield, eps, fundLeverageFactor, lastEarningsDate, nextDivExDate, nextDivPayDate, peRatio, sharesOutstanding
- `reference` — cusip, description (company name), exchange, exchangeName, isHardToBorrow, isShortable, htbRate
- `extended` — after-hours / pre-market quote data
- `regular` — regular session mark and price data

**Note: `sector` and `industry` are not returned by this endpoint.**
Company name is available as `reference.description`.

Top-level fields always present: `assetMainType`, `assetSubType`, `quoteType`, `realtime`, `ssid`, `symbol`.

**Example response** (`GET /quotes?symbols=AAPL&fields=quote,fundamental,reference`):
```json
{
  "count": 1,
  "data": {
    "AAPL": {
      "assetMainType": "EQUITY",
      "assetSubType": "COE",
      "quoteType": "NBBO",
      "realtime": true,
      "ssid": 1973757747,
      "symbol": "AAPL",
      "fundamental": {
        "avg10DaysVolume": 36372459,
        "avg1YearVolume": 53038980,
        "declarationDate": "2026-01-29T00:00:00Z",
        "divAmount": 1.04,
        "divExDate": "2026-02-09T00:00:00Z",
        "divFreq": 4,
        "divPayAmount": 0.26,
        "divPayDate": "2026-02-12T00:00:00Z",
        "divYield": 0.4161,
        "eps": 7.46,
        "fundLeverageFactor": 0,
        "lastEarningsDate": "2026-01-29T00:00:00Z",
        "nextDivExDate": "2026-05-11T00:00:00Z",
        "nextDivPayDate": "2026-05-12T00:00:00Z",
        "peRatio": 31.5756,
        "sharesOutstanding": 14681140000
      },
      "quote": {
        "52WeekHigh": 288.62,
        "52WeekLow": 169.2101,
        "askPrice": 249.87,
        "askSize": 300,
        "bidPrice": 249.6,
        "bidSize": 3500,
        "closePrice": 248.96,
        "highPrice": 249.1999,
        "lastPrice": 249.77,
        "lowPrice": 246,
        "mark": 247.99,
        "netChange": 0.81,
        "netPercentChange": 0.32535347,
        "openPrice": 247.975,
        "postMarketChange": 1.78,
        "postMarketPercentChange": 0.71777088,
        "totalVolume": 88331081
      },
      "reference": {
        "cusip": "037833100",
        "description": "APPLE INC",
        "exchange": "Q",
        "exchangeName": "NASDAQ",
        "isHardToBorrow": false,
        "isShortable": true,
        "htbRate": 0
      }
    }
  }
}
```

---

### GET /accounts

List linked accounts. Schwab addresses accounts by `hashValue`, not account number; use it as `{account_hash}` in the endpoints below.

**Response:** `{"count": 1, "data": [{"accountNumber": "12345678", "hashValue": "ABC..."}]}`

---

### GET /accounts/{account_hash}/orders

Order history for one account. Schwab's order objects are passed through unmodified.

| Param | Type | Default | Description |
|---|---|---|---|
| `from_date` | string | 60 days ago | `YYYY-MM-DD`, inclusive |
| `to_date` | string | today | `YYYY-MM-DD`, inclusive |
| `status` | string | all | One order status, e.g. `FILLED`, `WORKING`, `CANCELED`, `REJECTED`, `EXPIRED` |
| `max_results` | int | Schwab default | Maximum number of orders |

**Response:** `{"count": N, "data": [...]}`. `400` for an unknown `status`, `422` for a malformed date.

---

### GET /accounts/{account_hash}/transactions

Transactions for one account (fills, dividends, transfers). Schwab's objects are passed through unmodified.

| Param | Type | Default | Description |
|---|---|---|---|
| `from_date` | string | 60 days ago | `YYYY-MM-DD` |
| `to_date` | string | today | `YYYY-MM-DD` |
| `types` | string | `TRADE` | Comma-separated: `TRADE`, `DIVIDEND_OR_INTEREST`, `RECEIVE_AND_DELIVER`, `JOURNAL`, `ACH_RECEIPT`, `ACH_DISBURSEMENT`, `CASH_RECEIPT`, `CASH_DISBURSEMENT`, `ELECTRONIC_FUND`, `WIRE_IN`, `WIRE_OUT`, `MEMORANDUM`, `MARGIN_CALL`, `MONEY_MARKET`, `SMA_ADJUSTMENT` |
| `symbol` | string | all | Only transactions for this symbol |

**Response:** `{"count": N, "data": [...]}`. `400` for an unknown type.

---

### GET /instruments

Search for instruments or retrieve fundamental financial data.

| Param | Type | Default | Description |
|---|---|---|---|
| `symbols` | string | required | Comma-separated tickers or a search term, e.g. `SPY,AAPL` |
| `projection` | string | `fundamental` | One of: `fundamental`, `symbol-search`, `symbol-regex`, `desc-search`, `desc-regex`, `search` |

**Projection behaviour:**
- `fundamental` — exact symbol match(es), returns fundamental metrics (P/E, EPS, dividend yield, market cap, etc.)
- `symbol-search` — exact or partial symbol match
- `symbol-regex` — regex matched against symbols
- `desc-search` — string search in instrument description
- `desc-regex` — regex in instrument description
- `search` — general regex against symbols

**Response:** `{"projection": "fundamental", "count": 1, "data": {"instruments": [...]}}`

For `fundamental` projection, `data.instruments` is a list of objects. Each object has top-level fields `cusip`, `symbol`, `description` (company name), `exchange`, `assetType`, and a nested `fundamental` object.

`fundamental` object fields: `symbol`, `high52`, `low52`, `dividendAmount`, `dividendYield`, `dividendDate`, `peRatio`, `pegRatio`, `pbRatio`, `prRatio`, `pcfRatio`, `grossMarginTTM`, `grossMarginMRQ`, `netProfitMarginTTM`, `netProfitMarginMRQ`, `operatingMarginTTM`, `operatingMarginMRQ`, `returnOnEquity`, `returnOnAssets`, `returnOnInvestment`, `quickRatio`, `currentRatio`, `interestCoverage`, `totalDebtToCapital`, `ltDebtToEquity`, `totalDebtToEquity`, `epsTTM`, `epsChangePercentTTM`, `epsChangeYear`, `epsChange`, `revChangeYear`, `revChangeTTM`, `revChangeIn`, `sharesOutstanding`, `marketCapFloat`, `marketCap`, `bookValuePerShare`, `shortIntToFloat`, `shortIntDayToCover`, `divGrowthRate3Year`, `dividendPayAmount`, `dividendPayDate`, `beta`, `vol1DayAvg`, `vol10DayAvg`, `vol3MonthAvg`, `avg10DaysVolume`, `avg1DayVolume`, `avg3MonthVolume`, `declarationDate`, `dividendFreq`, `eps`, `nextDividendPayDate`, `nextDividendDate`, `fundLeverageFactor`.

**Example response** (`GET /instruments?symbols=AAPL&projection=fundamental`):
```json
{
  "projection": "fundamental",
  "count": 1,
  "data": {
    "instruments": [
      {
        "fundamental": {
          "symbol": "AAPL",
          "high52": 288.62,
          "low52": 169.2101,
          "dividendAmount": 1.04,
          "dividendYield": 0.41774,
          "peRatio": 31.45257,
          "pegRatio": 1.47612,
          "marketCap": 3655016614400,
          "beta": 1.11639,
          "epsTTM": 7.88457,
          "returnOnEquity": 152.0213,
          "grossMarginTTM": 47.3252,
          "sharesOutstanding": 14681140000
        },
        "cusip": "037833100",
        "symbol": "AAPL",
        "description": "APPLE INC",
        "exchange": "NASDAQ",
        "assetType": "EQUITY"
      }
    ]
  }
}
```

**Note:** `sector` and `industry` are not returned by this endpoint either. Use `description` (top-level field on each instrument object) for company name.

---

### GET /reauth

Generates the Schwab OAuth authorization URL. No parameters.

```json
{"auth_url": "https://api.schwabapi.com/v1/oauth/authorize?client_id=..."}
```

Open `auth_url` in a browser. Complete MFA login. Copy the full callback URL from the address bar.

---

### POST /reauth/complete

Exchanges the authorization code, writes token, reinitialises the client.

**Request body:**
```json
{"callback_url": "https://127.0.0.1?code=...&state=..."}
```

**Success response:**
```json
{"status": "ok", "message": "Token written and client reinitialised."}
```

---

### GET /reauth/status

Reports refresh token health without initiating a reauth attempt. No parameters.

```json
{"status": "ok", "remaining_days": 6.4}
```

`status` is one of `ok` / `expiring` / `expired` / `missing`. `remaining_days` is `null` when `status` is `missing`.

---

### GET /reauth/ui

Returns an HTML page (`text/html`, not JSON) — a human-facing reauth flow (status line, "Start login" button, paste box for the callback URL) for renewing the token without curl. Not meant for programmatic/agent consumption; use `/reauth` + `/reauth/complete` instead.

---

### GET /llm-docs

Returns this document as `text/plain`. Use to give an AI agent up-to-date API context.

---

### GET /docs

Swagger UI — interactive API explorer for human developers. Auto-generated by FastAPI from route definitions and docstrings.

### GET /openapi.json

Raw OpenAPI 3.x schema. Machine-readable endpoint/parameter definitions.

---

## WebSocket Streaming: WS /stream

Real-time market data via the Schwab StreamClient. One connection per session.

### Connection protocol

1. **Connect** to `ws://localhost:8182/stream`.
2. **Send** a JSON subscription spec within 10 seconds.
3. **Receive** `{"status": "subscribed", "count": N}` on success.
4. **Receive** stream messages indefinitely.
5. **Close** the connection when done — the gateway tears down the upstream Schwab WebSocket.

### Subscription spec

```json
{
  "subscriptions": [
    {"type": "level_one_equity",   "symbols": ["SPY", "QQQ"]},
    {"type": "chart_equity",       "symbols": ["SPY"]},
    {"type": "account_activity"}
  ]
}
```

`account_activity` requires no `symbols` field. All other types require a non-empty `symbols` list.

### Subscription types

| type | data returned |
|---|---|
| `level_one_equity` | Level 1 quote: bid, ask, last, size, volume, open, high, low, close, P/E, dividend yield, etc. |
| `chart_equity` | Real-time OHLCV chart bars for equities |
| `level_one_option` | Level 1 options quote |
| `level_one_futures` | Level 1 futures quote (use `/ES`, `/NQ` format) |
| `chart_futures` | Real-time OHLCV chart bars for futures |
| `level_one_forex` | Level 1 forex quote |
| `level_one_futures_options` | Level 1 futures options quote |
| `nyse_book` | NYSE order book (depth of market) |
| `nasdaq_book` | NASDAQ order book |
| `options_book` | Options order book |
| `screener_equity` | Top movers/actives for an equity index (symbol = screener key, e.g. `$DJI_PERCENT_CHANGE_UP_60`) |
| `screener_option` | Top movers/actives for options (symbol = screener key, e.g. `OPTION_PUT_VOLUME_30`) |
| `account_activity` | Account order/fill activity (no symbols needed) |

### Screener

Screener "symbols" are structured keys, **not** individual stock tickers.

**Equity screener key format:** `{INDEX}_{SORT_FIELD}_{FREQUENCY}`

| Part | Valid values |
|---|---|
| INDEX | `$DJI`, `$COMPX`, `$SPX.X`, `NASDAQ`, `NYSE`, `OTCBB`, `EQUITY_ALL` |
| SORT_FIELD | `VOLUME`, `TRADES`, `PERCENT_CHANGE_UP`, `PERCENT_CHANGE_DOWN` |
| FREQUENCY (minutes) | `0` (all day), `1`, `5`, `10`, `30`, `60` |

Example keys: `"$DJI_PERCENT_CHANGE_UP_60"`, `"NASDAQ_VOLUME_5"`, `"NYSE_TRADES_30"`

**Options screener key format:** `OPTION_{PUT|CALL}_{SORT_FIELD}_{FREQUENCY}`

Example keys: `"OPTION_PUT_PERCENT_CHANGE_UP_60"`, `"OPTION_CALL_VOLUME_30"`

**ScreenerFields** in each message item:

| Field | Meaning |
|---|---|
| `SYMBOL` | The screener key |
| `TIMESTAMP` | Market snapshot timestamp (epoch ms) |
| `SORT_FIELD` | The sort metric in effect |
| `FREQUENCY` | The frequency window in effect |
| `ITEMS` | Array of screener result objects (symbol, description, volume, last, net change, etc.) |

Example subscription spec:
```json
{
  "subscriptions": [
    {"type": "screener_equity", "symbols": ["$DJI_PERCENT_CHANGE_UP_60", "NASDAQ_VOLUME_5"]},
    {"type": "screener_option", "symbols": ["OPTION_PUT_PERCENT_CHANGE_UP_60"]}
  ]
}
```

Example screener message:
```json
{
  "service": "SCREENER_EQUITY",
  "content": {
    "service": "SCREENER_EQUITY",
    "timestamp": 1711022400000,
    "command": "SUBS",
    "content": [
      {
        "key": "$DJI_PERCENT_CHANGE_UP_60",
        "SORT_FIELD": "PERCENT_CHANGE_UP",
        "FREQUENCY": 60,
        "ITEMS": [
          {"symbol": "AAPL", "description": "Apple Inc", "last_price": 189.5, "net_change": 3.2}
        ]
      }
    ]
  }
}
```

### Stream message format

Each message forwarded to the client:
```json
{
  "service": "LEVELONE_EQUITIES",
  "content": {
    "service": "LEVELONE_EQUITIES",
    "timestamp": 1711022400000,
    "command": "SUBS",
    "content": [
      {"key": "SPY", "BID_PRICE": 591.23, "ASK_PRICE": 591.25, "LAST_PRICE": 591.24, ...}
    ]
  }
}
```

Field names in `content` are human-readable strings (not numeric codes).

### Reconnect messages

When the Schwab stream drops unexpectedly, the gateway attempts up to 3 reconnects with exponential backoff (2 s, 4 s, 8 s). Messages sent during this process:

```json
{"status": "reconnecting", "attempt": 1, "max_attempts": 3, "delay_seconds": 2.0}
{"status": "reconnected",  "attempt": 1}
{"error": "reconnect_failed", "detail": "Schwab stream failed after 3 reconnect attempts."}
```

If the frontend WebSocket closes during a reconnect attempt, the gateway stops reconnecting immediately.

### Error messages (sent before close)

```json
{"error": "no_client",    "detail": "Schwab client not initialised — complete the /reauth flow."}
{"error": "invalid_spec", "detail": "..."}
{"error": "stream_error", "detail": "..."}
```

---

## Token lifecycle

- Token file: `~/.schwab/token.json` (inside Docker volume `schwab_token` when deployed).
- Access token expires every **30 minutes** — schwab-py refreshes it automatically.
- Refresh token expires after **7 days** from initial OAuth grant.
- The background monitor checks every 12 hours; when fewer than 2 days remain it writes `~/.schwab/token_alert.txt`.
- `/health → token.refresh_expires_in_hours` always shows time remaining.
- When the refresh token expires, run the full reauth flow again.

---

## Error codes

| Code | Meaning |
|---|---|
| `400` | Bad request — invalid parameter value |
| `404` | No data found for the given symbol/date range |
| `422` | Validation error — missing required parameter |
| `502` | Schwab API returned an error |
| `503` | Schwab client not initialised — complete the reauth flow |
