import json

import pytest

import kis_auth
from kis_auth import KisAuthError, KisCredentials


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def creds(tmp_path, monkeypatch):
    monkeypatch.setattr(kis_auth, "TOKEN_DIR", tmp_path)
    return KisCredentials("paper", "key-1", "secret-1", "50123456", "01")


def test_parse_account_formats():
    assert kis_auth.parse_account("50123456-01") == ("50123456", "01")
    assert kis_auth.parse_account("5012345622") == ("50123456", "22")
    assert kis_auth.parse_account("50123456") == ("50123456", "01")
    with pytest.raises(KisAuthError):
        kis_auth.parse_account("5012-345")


def test_load_credentials_reports_missing_names_only(tmp_path, monkeypatch):
    for k in ("KIS_ENV", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("KIS_ENV=paper\nKIS_APP_KEY=abc\n", encoding="utf-8")
    with pytest.raises(KisAuthError, match="KIS_APP_SECRET, KIS_ACCOUNT"):
        kis_auth.load_credentials(env)
    env.write_text("KIS_ENV=paper\nKIS_APP_KEY=abc\nKIS_APP_SECRET=s3cret\nKIS_ACCOUNT=50123456\n", encoding="utf-8")
    c = kis_auth.load_credentials(env)
    assert (c.cano, c.acnt_prdt_cd, c.base_url.endswith(":29443")) == ("50123456", "01", True)
    assert "s3cret" not in repr(c) and "abc" not in repr(c)


def test_token_is_cached_until_an_hour_before_expiry(creds):
    calls = []

    def post(url, **kw):
        calls.append(url)
        return FakeResponse(200, {"access_token": f"tok{len(calls)}", "expires_in": 86400})

    assert kis_auth.get_token(creds, now=0, post=post)[0] == "tok1"
    assert kis_auth.get_token(creds, now=80_000, post=post)[0] == "tok1"   # 6400 s left: reuse
    assert kis_auth.get_token(creds, now=83_000, post=post)[0] == "tok2"   # 3400 s left: re-issue
    assert calls == [creds.base_url + "/oauth2/tokenP"] * 2


def test_new_app_key_does_not_reuse_old_token(creds):
    post = lambda url, **kw: FakeResponse(200, {"access_token": kw["json"]["appkey"], "expires_in": 86400})
    kis_auth.get_token(creds, now=0, post=post)
    other = KisCredentials("paper", "key-2", "secret-2", "50999999", "01")
    assert kis_auth.get_token(other, now=10, post=post)[0] == "key-2"


def test_token_error_surfaces_kis_message(creds):
    post = lambda url, **kw: FakeResponse(403, {"error_description": "유효하지 않은 AppKey입니다.", "error_code": "EGW00103"})
    with pytest.raises(KisAuthError, match="403.*AppKey"):
        kis_auth.get_token(creds, now=0, post=post)
