"""Reauth routes: GET /reauth, POST /reauth/complete.

Manual OAuth2 flow (no embedded Flask / Selenium):

1. ``GET /reauth``  — returns a Schwab authorization URL.
2. User opens the URL in a browser, authenticates with MFA, and is
   redirected to the callback URL.  They copy the full callback URL from
   the address bar.
3. ``POST /reauth/complete {"callback_url": "..."}`` — exchanges the
   authorization code for a token, writes ``token.json``, and
   reinitialises the Schwab client.
"""
import base64
import logging
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from gateway import client_state
from gateway.settings import get_settings
from gateway.token_utils import write_token

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
            detail=f"State mismatch — expected {_pending_state!r}, got {state!r}.",
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
