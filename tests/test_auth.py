import json
from dataclasses import replace

import pytest
from google.auth.exceptions import RefreshError

from newsletter_podcast import gmail_fetch
from newsletter_podcast.config import Config


def _write_token(path, *, expiry: str):
    path.write_text(
        json.dumps(
            {
                "client_id": "1234567890-abc.apps.googleusercontent.com",
                "client_secret": "secret",
                "refresh_token": "1//fake-refresh-token",
                "token": None,
                "expiry": expiry,
                "scopes": ["https://www.googleapis.com/auth/gmail.send"],
            }
        ),
        encoding="utf-8",
    )


def test_missing_token_points_at_auth_command(tmp_path):
    cfg = replace(Config.from_env(), token_file=tmp_path / "token.json")
    with pytest.raises(SystemExit, match="not authenticated|auth"):
        gmail_fetch.load_credentials(cfg)


def test_rejected_refresh_token_explains_testing_mode_expiry(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    _write_token(token, expiry="2020-01-01T00:00:00Z")
    cfg = replace(Config.from_env(), token_file=token)

    def reject(self, request):  # signature of Credentials.refresh
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(gmail_fetch.Credentials, "refresh", reject)

    with pytest.raises(RuntimeError) as excinfo:
        gmail_fetch.load_credentials(cfg)

    message = str(excinfo.value)
    assert "invalid_grant" in message
    assert "7 days" in message
    assert "reauth.sh" in message
