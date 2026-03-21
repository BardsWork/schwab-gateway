"""Pure functions for inspecting Schwab token age and expiry.

Token file schema::

    {
        "creation_timestamp": 1773937516,
        "token": {
            "expires_at": 1774010584,
            ...
        }
    }

The ``creation_timestamp`` is the Unix epoch when the token was first obtained.
Schwab refresh tokens expire 7 days after that moment.
"""
import json
import time
from datetime import datetime
from pathlib import Path

REFRESH_TOKEN_TTL_SECONDS: int = 7 * 86_400


def load_token(token_path: Path) -> dict:
    """Read and return the token dict from *token_path*."""
    with open(token_path) as fh:
        return json.load(fh)


def token_age_seconds(token: dict) -> float:
    """Seconds elapsed since the token was created."""
    return time.time() - token["creation_timestamp"]


def refresh_token_expires_in(token: dict) -> float:
    """Seconds until the 7-day refresh token expires (negative = already expired)."""
    return REFRESH_TOKEN_TTL_SECONDS - token_age_seconds(token)


def token_access_expires_at(token: dict) -> datetime:
    """Datetime when the current access token expires."""
    return datetime.fromtimestamp(token["token"]["expires_at"])


def write_token(token_path: Path, oauth_response: dict) -> None:
    """Persist a fresh OAuth token response to *token_path*.

    Adds ``creation_timestamp`` (now) and ``expires_at``
    (now + ``expires_in``) to match the schwab-py token format.

    Args:
        token_path: Destination file path (parent directory must exist).
        oauth_response: Raw token dict returned by the Schwab token endpoint.
    """
    now = int(time.time())
    token_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "creation_timestamp": now,
        "token": {
            **oauth_response,
            "expires_at": now + int(oauth_response.get("expires_in", 1800)),
        },
    }
    with open(token_path, "w") as fh:
        json.dump(payload, fh)
