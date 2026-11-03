"""KIS Open API auth: credentials from .env, access token cached outside the repo.

    python kis_auth.py      # issue (or reuse) a token and print its expiry; never prints secrets
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import dotenv_values

ENV_FILE = Path(__file__).with_name(".env")
BASES = {"paper": "https://openapivts.koreainvestment.com:29443",
         "real": "https://openapi.koreainvestment.com:9443"}
TOKEN_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "market-cap-rebalancer"
TOKEN_MIN_LEFT = 3600        # re-issue when less than this many seconds remain
DEFAULT_PRODUCT_CODE = "01"  # 종합계좌 주식


class KisAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class KisCredentials:
    env: str           # paper | real
    app_key: str
    app_secret: str
    cano: str          # 계좌번호 앞 8자리
    acnt_prdt_cd: str  # 계좌상품코드 2자리

    @property
    def base_url(self) -> str:
        return BASES[self.env]

    def __repr__(self) -> str:  # keep secrets out of logs and tracebacks
        return f"KisCredentials(env={self.env!r}, cano=****{self.cano[-2:]}, acnt_prdt_cd={self.acnt_prdt_cd!r})"


def parse_account(raw: str) -> tuple[str, str]:
    """'12345678-01', '1234567801' or '12345678' (product code defaults to 01)."""
    digits = re.sub(r"[\s-]", "", raw)
    if re.fullmatch(r"\d{10}", digits):
        return digits[:8], digits[8:]
    if re.fullmatch(r"\d{8}", digits):
        return digits, DEFAULT_PRODUCT_CODE
    raise KisAuthError("KIS_ACCOUNT must be 8 digits, optionally followed by a 2-digit product code")


def load_credentials(path: Path = ENV_FILE) -> KisCredentials:
    values = {**dotenv_values(path), **{k: v for k, v in os.environ.items() if k.startswith("KIS_")}}
    missing = [k for k in ("KIS_ENV", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT") if not values.get(k)]
    if missing:
        raise KisAuthError(f"missing in {path.name} or environment: {', '.join(missing)}")
    env = values["KIS_ENV"].strip().lower()
    if env not in BASES:
        raise KisAuthError(f"KIS_ENV must be one of {sorted(BASES)}")
    cano, prdt = parse_account(values["KIS_ACCOUNT"])
    return KisCredentials(env, values["KIS_APP_KEY"].strip(), values["KIS_APP_SECRET"].strip(), cano, prdt)


def _cache_path(creds: KisCredentials) -> Path:
    # keyed by app key hash, so swapping keys (e.g. a renewed paper account) never reuses a stale token
    return TOKEN_DIR / f"token-{creds.env}-{hashlib.sha256(creds.app_key.encode()).hexdigest()[:12]}.json"


def get_token(creds: KisCredentials, now: float | None = None, post=requests.post) -> tuple[str, float]:
    """Returns (access_token, expires_at epoch). Reuses the cached token while it has time left;
    KIS limits issuance, so callers should never request a token per API call."""
    now = time.time() if now is None else now
    path = _cache_path(creds)
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached["expires_at"] - now > TOKEN_MIN_LEFT:
            return cached["access_token"], cached["expires_at"]

    r = post(f"{creds.base_url}/oauth2/tokenP", timeout=10,
             json={"grant_type": "client_credentials", "appkey": creds.app_key, "appsecret": creds.app_secret})
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or "access_token" not in body:
        reason = body.get("error_description") or body.get("msg1") or r.text[:200]
        raise KisAuthError(f"token request failed ({r.status_code}): {reason}")

    expires_at = now + int(body.get("expires_in", 86400))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"access_token": body["access_token"], "expires_at": expires_at}), encoding="utf-8")
    return body["access_token"], expires_at


def auth_headers(creds: KisCredentials, token: str, tr_id: str) -> dict[str, str]:
    return {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
            "appkey": creds.app_key, "appsecret": creds.app_secret, "tr_id": tr_id, "custtype": "P"}


if __name__ == "__main__":
    c = load_credentials()
    _, exp = get_token(c)
    print(f"token ok ({c.env}), expires {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp))}, "
          f"cache {_cache_path(c).parent}")
