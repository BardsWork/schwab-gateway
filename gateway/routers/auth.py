"""Reauth routes: GET /reauth, POST /reauth/complete, plus a browser-friendly UI.

Manual OAuth2 flow (no embedded Flask / Selenium):

1. ``GET /reauth``  — returns a Schwab authorization URL.
2. User opens the URL in a browser, authenticates with MFA, and is
   redirected to the callback URL.  They copy the full callback URL from
   the address bar.
3. ``POST /reauth/complete {"callback_url": "..."}`` — exchanges the
   authorization code for a token, writes ``token.json``, and
   reinitialises the Schwab client.

``GET /reauth/ui`` wraps steps 1-3 in a single HTML page (button + paste
box) so renewing the token doesn't require hand-crafting curl commands.
``GET /reauth/status`` backs that page's status line and can also be
polled directly.
"""
import base64
import logging
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from gateway import client_state
from gateway.settings import get_settings
from gateway.token_utils import load_token, refresh_token_expires_in, write_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Auth"])

_SCHWAB_AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
_SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"

# Single-user service: store pending OAuth state in memory
_pending_state: str | None = None


@router.get("/reauth")
def reauth() -> dict[str, str]:
    """Generate a Schwab OAuth2 authorization URL.

    Returns:
        ``{"auth_url": "https://api.schwabapi.com/v1/oauth/authorize?..."}``

    After opening the URL and completing login, copy the full callback URL
    from your browser's address bar and POST it to ``/reauth/complete``.
    """
    global _pending_state
    settings = get_settings()

    _pending_state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": settings.schwab_app_key,
        "redirect_uri": settings.schwab_callback_url,
        "scope": "readonly",
        "state": _pending_state,
    }
    auth_url = f"{_SCHWAB_AUTH_URL}?{urlencode(params)}"
    logger.info("Reauth initiated — state=%s", _pending_state)
    return {"auth_url": auth_url}


class ReauthCompleteRequest(BaseModel):
    callback_url: str


@router.post("/reauth/complete")
def reauth_complete(body: ReauthCompleteRequest) -> dict[str, str]:
    """Exchange the authorization code and reinitialise the Schwab client.

    Args:
        body: JSON with ``callback_url`` — the full URL the browser landed on
              after Schwab login (e.g. ``https://127.0.0.1?code=...&state=...``).

    Returns:
        ``{"status": "ok", "message": "..."}``
    """
    global _pending_state
    settings = get_settings()

    if _pending_state is None:
        raise HTTPException(
            status_code=400, detail="No pending reauth — call GET /reauth first."
        )

    parsed = urlparse(body.callback_url)
    qs = parse_qs(parsed.query)

    code = qs.get("code", [None])[0]
    state = qs.get("state", [None])[0]

    if not code:
        raise HTTPException(status_code=400, detail="'code' not found in callback_url.")

    if state != _pending_state:
        raise HTTPException(
            status_code=400,
            detail="State mismatch — call GET /reauth again and use the new URL.",
        )

    # Exchange authorization code for token
    credentials = f"{settings.schwab_app_key}:{settings.schwab_api_secret}"
    auth_header = base64.b64encode(credentials.encode()).decode()

    try:
        resp = httpx.post(
            _SCHWAB_TOKEN_URL,
            headers={"Authorization": f"Basic {auth_header}"},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.schwab_callback_url,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Schwab token exchange failed: {exc.response.status_code} — {exc.response.text}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502, detail=f"Token exchange request failed: {exc}"
        ) from exc

    token_data = resp.json()
    write_token(settings.token_path, token_data)
    logger.info("Token written to %s", settings.token_path)

    _pending_state = None
    client_state.reset_client()
    logger.info("Schwab client reinitialised after reauth")

    return {"status": "ok", "message": f"Token written to {settings.token_path}. Client reinitialised."}


@router.get("/reauth/status")
def reauth_status() -> dict[str, str | float | None]:
    """Report the refresh token's health without requiring a reauth attempt.

    Returns:
        ``{"status": "ok" | "expiring" | "expired" | "missing", "remaining_days": float | None}``
    """
    settings = get_settings()

    if not settings.token_path.exists():
        return {"status": "missing", "remaining_days": None}

    try:
        token = load_token(settings.token_path)
        remaining_days = refresh_token_expires_in(token) / 86_400
    except Exception as exc:
        logger.error("reauth_status: failed to read token — %s", exc)
        return {"status": "missing", "remaining_days": None}

    if remaining_days <= 0:
        status = "expired"
    elif remaining_days < settings.alert_threshold_days:
        status = "expiring"
    else:
        status = "ok"

    return {"status": status, "remaining_days": round(remaining_days, 2)}


@router.get("/reauth/ui", response_class=HTMLResponse)
def reauth_ui() -> str:
    """Browser-friendly reauth page: no curl commands required.

    Click "Start login", complete Schwab's MFA flow, then paste the
    address-bar URL you land on back into the page and click "Complete".
    """
    return _REAUTH_UI_HTML


_REAUTH_UI_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>schwab-gateway reauth</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 640px; margin: 3rem auto; padding: 0 1rem; color: #1a1a1a; }
  h1 { font-size: 1.25rem; }
  .status { padding: 0.75rem 1rem; border-radius: 6px; margin-bottom: 1.5rem; font-size: 0.95rem; }
  .status.ok { background: #e6f4ea; color: #1e4620; }
  .status.expiring { background: #fef7e0; color: #7a5a00; }
  .status.expired, .status.missing { background: #fce8e6; color: #8a1c1c; }
  .step { margin-bottom: 1.5rem; }
  .step h2 { font-size: 1rem; margin-bottom: 0.4rem; }
  button { font-size: 0.95rem; padding: 0.5rem 1rem; cursor: pointer; }
  input[type=text] { width: 100%; box-sizing: border-box; padding: 0.5rem; font-size: 0.9rem; margin: 0.5rem 0; }
  .msg { font-size: 0.9rem; margin-top: 0.5rem; white-space: pre-wrap; }
  .msg.error { color: #8a1c1c; }
  .msg.ok { color: #1e4620; }
  code { background: #f0f0f0; padding: 0.1rem 0.3rem; border-radius: 3px; }
</style>
</head>
<body>
<h1>Schwab gateway — renew token</h1>
<div id="status" class="status">Checking token status…</div>

<div class="step">
  <h2>1. Start login</h2>
  <button id="startBtn">Start login</button>
  <span id="startMsg" class="msg"></span>
</div>

<div class="step">
  <h2>2. Finish it</h2>
  <p>Schwab will redirect you to a page that may look broken (e.g. <code>https://127.0.0.1?code=...</code>)
     — that's expected. Copy the <strong>full URL</strong> from your browser's address bar and paste it below.</p>
  <input type="text" id="callbackUrl" placeholder="https://127.0.0.1?code=...&state=...">
  <button id="completeBtn">Complete</button>
  <div id="completeMsg" class="msg"></div>
</div>

<script>
async function refreshStatus() {
  const el = document.getElementById('status');
  try {
    const res = await fetch('/reauth/status');
    const data = await res.json();
    el.className = 'status ' + data.status;
    if (data.status === 'ok') {
      el.textContent = `Token OK — refresh token valid for ${data.remaining_days} more day(s).`;
    } else if (data.status === 'expiring') {
      el.textContent = `Expiring soon — ${data.remaining_days} day(s) left. Renew now.`;
    } else if (data.status === 'expired') {
      el.textContent = 'Refresh token has expired. Renew below.';
    } else {
      el.textContent = 'No token file found yet. Complete the steps below.';
    }
  } catch (err) {
    el.className = 'status expired';
    el.textContent = 'Could not reach /reauth/status: ' + err;
  }
}

document.getElementById('startBtn').addEventListener('click', async () => {
  const msg = document.getElementById('startMsg');
  msg.className = 'msg';
  msg.textContent = 'Opening Schwab login…';
  try {
    const res = await fetch('/reauth');
    const data = await res.json();
    window.open(data.auth_url, '_blank');
    msg.textContent = 'Opened in a new tab. Log in, then come back and do step 2.';
  } catch (err) {
    msg.className = 'msg error';
    msg.textContent = 'Failed to start login: ' + err;
  }
});

document.getElementById('completeBtn').addEventListener('click', async () => {
  const msg = document.getElementById('completeMsg');
  const callbackUrl = document.getElementById('callbackUrl').value.trim();
  msg.className = 'msg';
  if (!callbackUrl) {
    msg.className = 'msg error';
    msg.textContent = 'Paste the callback URL first.';
    return;
  }
  msg.textContent = 'Exchanging code…';
  try {
    const res = await fetch('/reauth/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ callback_url: callbackUrl }),
    });
    const data = await res.json();
    if (res.ok) {
      msg.className = 'msg ok';
      msg.textContent = data.message || 'Token renewed.';
      refreshStatus();
    } else {
      msg.className = 'msg error';
      msg.textContent = data.detail || 'Token exchange failed.';
    }
  } catch (err) {
    msg.className = 'msg error';
    msg.textContent = 'Request failed: ' + err;
  }
});

refreshStatus();
</script>
</body>
</html>
"""
