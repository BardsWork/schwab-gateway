"""Background asyncio task that monitors refresh-token expiry.

Runs every 12 hours.  If the refresh token will expire within
``settings.alert_threshold_days`` days:

* Writes ``~/.schwab/token_alert.txt`` with a human-readable warning.
* Optionally sends a local ``mail`` notification (if ``/usr/sbin/sendmail``
  is available).
"""
import asyncio
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from gateway.settings import get_settings
from gateway.token_utils import load_token, refresh_token_expires_in

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 12 * 3600  # 12 hours


async def run_token_monitor() -> None:
    """Asyncio background task — loops indefinitely, checking token expiry."""
    logger.info("Token monitor started (check interval: %dh).", _CHECK_INTERVAL_SECONDS // 3600)
    while True:
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
        _check_expiry()


def _check_expiry() -> None:
    settings = get_settings()

    if not settings.token_path.exists():
        logger.warning("Token monitor: token file not found at %s.", settings.token_path)
        return

    try:
        token = load_token(settings.token_path)
    except Exception as exc:
        logger.error("Token monitor: failed to load token — %s", exc)
        return

    remaining_seconds = refresh_token_expires_in(token)
    remaining_days = remaining_seconds / 86_400

    if remaining_seconds < settings.alert_threshold_days * 86_400:
        _send_alert(remaining_days, settings.token_path)
    else:
        logger.debug(
            "Token monitor: refresh token expires in %.1f days — OK.", remaining_days
        )


def _send_alert(remaining_days: float, token_path: Path) -> None:
    alert_file = token_path.parent / "token_alert.txt"
    status = "EXPIRED" if remaining_days <= 0 else f"expires in {remaining_days:.1f} days"
    message = (
        f"[schwab-gateway] Refresh token alert — {datetime.now().isoformat()}\n"
        f"Status  : {status}\n"
        f"Action  : Run  GET /reauth  then  POST /reauth/complete  to renew.\n"
    )

    try:
        alert_file.write_text(message)
        logger.warning("Token alert written to %s", alert_file)
    except OSError as exc:
        logger.error("Could not write token alert file: %s", exc)

    # Best-effort local mail notification
    try:
        result = subprocess.run(
            ["mail", "-s", "[schwab-gateway] refresh token expiry warning", "root"],
            input=message,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            logger.info("Token expiry mail notification sent.")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # mail not configured — alert file is sufficient
