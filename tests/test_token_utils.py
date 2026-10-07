"""Unit tests for gateway.token_utils — no credentials or network needed."""
import json
import time

import pytest

from gateway.token_utils import (
    REFRESH_TOKEN_TTL_SECONDS,
    load_token,
    refresh_token_expires_in,
    token_access_expires_at,
    token_age_seconds,
    write_token,
)


def _make_token(age_seconds: float = 3600, expires_at_offset: float = 1800) -> dict:
    now = time.time()
    return {
        "creation_timestamp": now - age_seconds,
        "token": {
            "expires_in": 1800,
            "token_type": "Bearer",
            "scope": "api",
            "refresh_token": "r",
            "access_token": "a",
            "id_token": "i",
            "expires_at": now + expires_at_offset,
        },
    }


class TestTokenAgeSeconds:
    def test_zero_when_just_created(self):
        token = _make_token(age_seconds=0)
        assert token_age_seconds(token) == pytest.approx(0, abs=1)

    def test_reflects_age(self):
        token = _make_token(age_seconds=7200)
        assert token_age_seconds(token) == pytest.approx(7200, abs=2)


class TestRefreshTokenExpiresIn:
    def test_fresh_token_has_full_ttl(self):
        token = _make_token(age_seconds=0)
        remaining = refresh_token_expires_in(token)
        assert remaining == pytest.approx(REFRESH_TOKEN_TTL_SECONDS, abs=2)

    def test_6_day_old_token_has_1_day_left(self):
        token = _make_token(age_seconds=6 * 86_400)
        remaining = refresh_token_expires_in(token)
        assert remaining == pytest.approx(86_400, abs=2)

    def test_expired_token_is_negative(self):
        token = _make_token(age_seconds=8 * 86_400)
        assert refresh_token_expires_in(token) < 0


class TestTokenAccessExpiresAt:
    def test_returns_datetime_in_future(self):
        from datetime import datetime, timezone
        token = _make_token(expires_at_offset=900)
        expires = token_access_expires_at(token)
        assert isinstance(expires, datetime)
        assert expires > datetime.now(timezone.utc)

    def test_correct_timestamp(self):
        from datetime import datetime, timezone
        now = time.time()
        token = {"token": {"expires_at": now + 1800}}
        expires = token_access_expires_at(token)
        assert expires == pytest.approx(
            datetime.fromtimestamp(now + 1800, tz=timezone.utc), abs=1
        )


class TestLoadToken:
    def test_loads_from_file(self, tmp_path):
        token = _make_token()
        p = tmp_path / "token.json"
        p.write_text(json.dumps(token))
        loaded = load_token(p)
        assert loaded["creation_timestamp"] == pytest.approx(
            token["creation_timestamp"], abs=1
        )

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_token(tmp_path / "missing.json")


class TestWriteToken:
    def test_writes_valid_format(self, tmp_path):
        p = tmp_path / "token.json"
        oauth_resp = {
            "access_token": "acc",
            "refresh_token": "ref",
            "token_type": "Bearer",
            "expires_in": 1800,
            "scope": "api",
            "id_token": "id",
        }
        write_token(p, oauth_resp)
        data = json.loads(p.read_text())
        assert "creation_timestamp" in data
        assert data["token"]["access_token"] == "acc"
        assert data["token"]["expires_at"] == pytest.approx(
            time.time() + 1800, abs=5
        )

    def test_creates_parent_directory(self, tmp_path):
        p = tmp_path / "nested" / "dir" / "token.json"
        write_token(p, {"access_token": "a", "expires_in": 1800})
        assert p.exists()

    def test_file_is_owner_only(self, tmp_path):
        p = tmp_path / "token.json"
        write_token(p, {"access_token": "a", "expires_in": 1800})
        assert p.stat().st_mode & 0o777 == 0o600
