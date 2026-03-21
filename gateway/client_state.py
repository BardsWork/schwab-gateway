"""Schwab client singleton.

Initialised at startup via ``init_client()``.  After a successful reauth,
``reset_client()`` reinitialises from the refreshed token file.

All data routes call ``get_client()``; if the client is not yet initialised
they receive an HTTPException(503) from the router layer.
"""
import logging
import threading
from typing import Any

import schwab

from gateway.settings import get_settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_client: Any = None  # schwab.client.Client | None


def get_client() -> Any:
    """Return the active Schwab client, or raise RuntimeError if not initialised."""
    if _client is None:
        raise RuntimeError("Schwab client not initialised — complete the /reauth flow.")
    return _client


def init_client() -> None:
    """Initialise the Schwab client from the persisted token file.

    Logs a warning (does not raise) if the token file is absent, so the
    service can still start and serve the reauth endpoints.
    """
    global _client
    settings = get_settings()

    if not settings.token_path.exists():
        logger.warning(
            "Token file not found at %s — data endpoints will return 503 "
            "until the reauth flow is completed.",
            settings.token_path,
        )
        return

    with _lock:
        _client = schwab.auth.client_from_token_file(
            token_path=str(settings.token_path),
            api_key=settings.schwab_app_key,
            app_secret=settings.schwab_api_secret,
        )
    logger.info("Schwab client initialised from %s", settings.token_path)


def reset_client() -> None:
    """Reinitialise the client after a successful reauth."""
    global _client
    with _lock:
        _client = None
    init_client()
