"""Tests for gateway.monitoring.token_monitor."""
import asyncio
import json
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gateway.monitoring import token_monitor
from gateway.monitoring.token_monitor import _check_expiry, _send_alert
from gateway.settings import Settings


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_token(path: Path, age_seconds: float = 3600) -> None:
    now = time.time()
    path.write_text(json.dumps({
        "creation_timestamp": now - age_seconds,
        "token": {
            "expires_in": 1800,
            "token_type": "Bearer",
            "scope": "api",
            "refresh_token": "r",
            "access_token": "a",
            "id_token": "i",
            "expires_at": now + 1800,
        },
    }))


def _patch_settings(monkeypatch, token_path: Path) -> None:
    monkeypatch.setattr(
        "gateway.settings._settings",
        Settings(
            schwab_app_key="k",
            schwab_api_secret="s",
            schwab_callback_url="https://127.0.0.1",
            token_path=token_path,
        ),
    )


# ── run_token_monitor ─────────────────────────────────────────────────────────

class TestRunTokenMonitor:
    @pytest.mark.asyncio
    async def test_calls_check_expiry_after_sleep(self, monkeypatch):
        """run_token_monitor should sleep then call _check_expiry each iteration."""
        calls = []

        async def instant_sleep(_seconds):
            pass

        def mock_check():
            calls.append(1)
            raise asyncio.CancelledError()  # exit after first call

        monkeypatch.setattr(token_monitor.asyncio, "sleep", instant_sleep)
        monkeypatch.setattr(token_monitor, "_check_expiry", mock_check)

        with pytest.raises(asyncio.CancelledError):
            await token_monitor.run_token_monitor()

        assert len(calls) == 1


# ── _check_expiry ─────────────────────────────────────────────────────────────

class TestCheckExpiry:
    def test_no_token_file_logs_warning(self, tmp_path, monkeypatch, caplog):
        _patch_settings(monkeypatch, tmp_path / "nonexistent.json")
        with caplog.at_level(logging.WARNING, logger="gateway.monitoring.token_monitor"):
            _check_expiry()
        assert any("not found" in r.message for r in caplog.records)

    def test_corrupt_token_file_logs_error(self, tmp_path, monkeypatch, caplog):
        token_path = tmp_path / "token.json"
        token_path.write_text("not valid json {{{")
        _patch_settings(monkeypatch, token_path)
        with caplog.at_level(logging.ERROR, logger="gateway.monitoring.token_monitor"):
            _check_expiry()
        assert any("failed to load" in r.message for r in caplog.records)

    def test_healthy_token_does_not_send_alert(self, tmp_path, monkeypatch):
        token_path = tmp_path / "token.json"
        _write_token(token_path, age_seconds=3600)  # 1 h old → ~6.96 days left
        _patch_settings(monkeypatch, token_path)
        with patch.object(token_monitor, "_send_alert") as mock_alert:
            _check_expiry()
        mock_alert.assert_not_called()

    def test_expiring_token_calls_send_alert(self, tmp_path, monkeypatch):
        # 6.5 days old → 0.5 days left, below default 2.0-day threshold
        token_path = tmp_path / "token.json"
        _write_token(token_path, age_seconds=6.5 * 86_400)
        _patch_settings(monkeypatch, token_path)
        with patch.object(token_monitor, "_send_alert") as mock_alert:
            _check_expiry()
        mock_alert.assert_called_once()
        remaining_days = mock_alert.call_args[0][0]
        assert remaining_days < 1.0


# ── _send_alert ───────────────────────────────────────────────────────────────

class TestSendAlert:
    def test_writes_alert_file(self, tmp_path):
        token_path = tmp_path / "token.json"
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            _send_alert(remaining_days=0.5, token_path=token_path)
        alert_file = tmp_path / "token_alert.txt"
        assert alert_file.exists()
        content = alert_file.read_text()
        assert "schwab-gateway" in content
        assert "reauth" in content.lower()

    def test_alert_message_contains_days_remaining(self, tmp_path):
        token_path = tmp_path / "token.json"
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            _send_alert(remaining_days=1.23, token_path=token_path)
        content = (tmp_path / "token_alert.txt").read_text()
        assert "1.2" in content

    def test_expired_token_shows_expired_status(self, tmp_path):
        token_path = tmp_path / "token.json"
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            _send_alert(remaining_days=-0.1, token_path=token_path)
        content = (tmp_path / "token_alert.txt").read_text()
        assert "EXPIRED" in content

    def test_mail_success_is_logged(self, tmp_path, caplog):
        token_path = tmp_path / "token.json"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            with caplog.at_level(logging.INFO, logger="gateway.monitoring.token_monitor"):
                _send_alert(remaining_days=0.5, token_path=token_path)
        assert any("mail" in r.message.lower() for r in caplog.records)

    def test_mail_not_installed_is_silenced(self, tmp_path):
        """Missing mail binary should not propagate — alert file is sufficient."""
        token_path = tmp_path / "token.json"
        with patch("subprocess.run", side_effect=FileNotFoundError):
            _send_alert(remaining_days=0.5, token_path=token_path)  # must not raise

    def test_mail_timeout_is_silenced(self, tmp_path):
        import subprocess
        token_path = tmp_path / "token.json"
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="mail", timeout=5)):
            _send_alert(remaining_days=0.5, token_path=token_path)  # must not raise

    def test_alert_file_write_failure_logs_error(self, caplog):
        """If the alert directory is unwritable, log an error but don't raise."""
        token_path = Path("/nonexistent_root_dir/token.json")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with caplog.at_level(logging.ERROR, logger="gateway.monitoring.token_monitor"):
                _send_alert(remaining_days=0.5, token_path=token_path)
        assert any("Could not write" in r.message for r in caplog.records)
