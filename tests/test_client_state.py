"""Tests for gateway.client_state."""
import logging

import gateway.client_state as cs
from gateway.settings import Settings


class TestInitClient:
    def test_no_token_file_keeps_client_none_and_logs_warning(
        self, tmp_path, monkeypatch, caplog
    ):
        """init_client should not raise when token file is absent — just warn."""
        monkeypatch.setattr(
            "gateway.settings._settings",
            Settings(
                schwab_app_key="k",
                schwab_api_secret="s",
                schwab_callback_url="https://127.0.0.1",
                token_path=tmp_path / "nonexistent.json",
            ),
        )
        monkeypatch.setattr(cs, "_client", None)

        with caplog.at_level(logging.WARNING, logger="gateway.client_state"):
            cs.init_client()

        assert cs._client is None
        assert any("not found" in r.message for r in caplog.records)
