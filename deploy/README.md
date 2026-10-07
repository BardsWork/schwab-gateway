# Deployment Notes

## Target environment

Any Linux host with Docker and Compose v2 (developed on Ubuntu 22.04, Docker 28). Docker daemon starts on boot,
so `restart: always` is sufficient process supervision (no systemd unit needed).

## First-time setup

```bash
cd /path/to/schwab-gateway
cp .env.example .env
# Fill in SCHWAB_APP_KEY, SCHWAB_API_SECRET, SCHWAB_CALLBACK_URL
docker network create schwab-net   # once; other stacks join it to reach the gateway
docker compose up -d --build
```

The named volume `schwab_token` is created empty. Complete the reauth flow
immediately to populate it.

**Browser (recommended):** open `http://localhost:8182/reauth/ui` and follow
the two steps on the page — no curl needed. Same flow whenever the refresh
token expires later (every 7 days).

**curl:**

```bash
# 1. Get the Schwab authorization URL
curl http://localhost:8182/reauth

# 2. Open the returned auth_url in a browser, log in (MFA as normal).
#    You will be redirected to your callback URL (e.g. https://127.0.0.1?code=...&state=...).
#    Copy the full URL from the address bar.

# 3. Exchange the code
curl -X POST http://localhost:8182/reauth/complete \
  -H "Content-Type: application/json" \
  -d '{"callback_url": "https://127.0.0.1?code=YOUR_CODE&state=YOUR_STATE"}'

# 4. Verify
curl http://localhost:8182/reauth/status
# {"status": "ok", "remaining_days": 7.0}
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

When the alert fires, open `/reauth/ui` or repeat steps 1–4 above from any
machine that can reach the host.

## Port and network access

The service listens on **8182** on all interfaces and has no authentication.
Keep the host on a trusted network, or publish the port as
`127.0.0.1:8182:8182` in `docker-compose.yml` to limit it to the host itself.
