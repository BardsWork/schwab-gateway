"""GET /health — service liveness + token expiry information."""
import logging
from typing import Any

from fastapi import APIRouter

from gateway import client_state
from gateway.settings import get_settings
from gateway.token_utils import (
    load_token,
    refresh_token_expires_at,
    refresh_token_expires_in,
    token_age_seconds,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    """Return service status and token expiry metadata."""
    settings = get_settings()
    client_ready = client_state._client is not None

    token_info: dict[str, Any] = {}
    if settings.token_path.exists():
        try:
            token = load_token(settings.token_path)
            age_h = token_age_seconds(token) / 3600
            refresh_left_h = refresh_token_expires_in(token) / 3600
            refresh_expires = refresh_token_expires_at(token).isoformat()
            token_info = {
                "age_hours": round(age_h, 2),
                "refresh_expires_in_hours": round(refresh_left_h, 2),
                "access_expires_at": refresh_expires,
            }
        except Exception as exc:
            token_info = {"error": str(exc)}
    else:
        token_info = {"error": "token file not found"}

    return {
        "status": "ok" if client_ready else "degraded",
        "client_ready": client_ready,
        "token": token_info,
    }
