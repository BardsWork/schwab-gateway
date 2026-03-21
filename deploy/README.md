# Deployment Notes

## Target environment

Ubuntu 22.04 devbox — Docker 28, Compose v2. Docker daemon starts on boot,
so `restart: always` is sufficient process supervision (no systemd unit needed).

## First-time setup

```bash
cd /path/to/schwab-gateway
cp .env.example .env
# Fill in SCHWAB_APP_KEY, SCHWAB_API_SECRET, SCHWAB_CALLBACK_URL
docker compose up -d --build
```

The named volume `schwab_token` is created empty. Complete the reauth flow
immediately to populate it:

```bash
# 1. Get the Schwab authorization URL
curl http://devbox.local:8182/reauth

# 2. Open the returned auth_url in a browser, log in (MFA as normal).
#    You will be redirected to your callback URL (e.g. https://127.0.0.1?code=...&state=...).
#    Copy the full URL from the address bar.

# 3. Exchange the code
curl -X POST http://devbox.local:8182/reauth/complete \
  -H "Content-Type: application/json" \
  -d '{"callback_url": "https://127.0.0.1?code=YOUR_CODE&state=YOUR_STATE"}'

# 4. Verify
curl http://devbox.local:8182/health
# age_hours should be ~0
```

## Routine operations

```bash
# View logs
docker compose logs -f

# Rebuild after code changes
docker compose up -d --build

# Stop
docker compose down

# Stop and remove the token volume (forces full reauth on next start)
docker compose down -v
```

## Token renewal (every 7 days)

The background monitor checks every 12 hours and writes
`~/.schwab/token_alert.txt` (inside the container volume) when fewer than
`ALERT_THRESHOLD_DAYS` (default 2) days remain. The `/health` endpoint also
shows `refresh_expires_in_hours`.

When the alert fires, repeat steps 1–4 above from any machine that can reach
the devbox.

## Port

The service listens on **8182** by default. Accessible at
`http://devbox.local:8182` (or `http://192.168.1.177:8182`) from any machine
on the local network.

## Updating derivatives-analysis notebooks

Notebooks no longer need `schwab-py` or a local `token.json`. Replace:

```python
from api.schwab.client import get_client, fetch_bars
client = get_client()
df = fetch_bars(client, "SPY", "2026-03-17", "2026-03-21", frequency=5)
```

with:

```python
from api.schwab.gateway_client import fetch_bars
df = fetch_bars("SPY", "2026-03-17", "2026-03-21", frequency=5)
```
