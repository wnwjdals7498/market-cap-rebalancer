"""Telegram notifications. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env; unset = disabled.

    python notify.py --chat-ids   # after sending your bot any message: list chats it can reach
    python notify.py --test       # send a test message
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import requests
from dotenv import dotenv_values

log = logging.getLogger("mcr")

ENV_FILE = Path(__file__).with_name(".env")
API = "https://api.telegram.org/bot{token}/{method}"
PREFIX = "[MCR]"
MAX_LEN = 4000  # Telegram caps messages at 4096 chars

# KIS error codes worth an explanation in the alert
HINTS = {
    "40910000": "모의투자 계좌 기간 만료 → 새 모의계좌 발급, KIS Developers 재신청, .env 앱키·계좌 교체",
    "90070000": "계좌와 HTS ID 불일치 → 모의 계좌번호(5로 시작)인지, 실전/모의 앱키가 맞는지 확인",
    "EGW00103": "유효하지 않은 앱키 → .env 의 KIS_APP_KEY / KIS_ENV 확인",
    "EGW00105": "유효하지 않은 앱시크릿 → .env 의 KIS_APP_SECRET 확인",
    "EGW00123": "토큰 만료 → 다음 실행에서 재발급됨, 반복되면 토큰 캐시 삭제",
    "EGW00201": "초당 호출 한도 초과가 재시도 후에도 지속",
}


def _config() -> tuple[str, str] | None:
    values = {**dotenv_values(ENV_FILE), **{k: v for k, v in os.environ.items() if k.startswith("TELEGRAM_")}}
    token = (values.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (values.get("TELEGRAM_CHAT_ID") or "").strip()
    return (token, chat) if token and chat else None


def hint(text: str) -> str:
    """Explanation for any known KIS error code mentioned in `text` ('' if none)."""
    return "; ".join(h for code, h in HINTS.items() if code in text)


def send(text: str, post=requests.post) -> bool:
    """Best effort: never raises, so an alert problem cannot break a trading job."""
    cfg = _config()
    if cfg is None:
        return False
    token, chat = cfg
    try:
        r = post(API.format(token=token, method="sendMessage"), timeout=10,
                 json={"chat_id": chat, "text": f"{PREFIX} {text}"[:MAX_LEN], "disable_web_page_preview": True})
    except requests.RequestException as e:  # the message may contain the URL, i.e. the token
        log.warning("telegram send failed: %s", type(e).__name__)
        return False
    if r.status_code != 200:
        log.warning("telegram send failed: HTTP %s %s", r.status_code, r.text[:200].replace(token, "***"))
        return False
    return True


def _chat_ids() -> None:
    cfg = _config()
    token = cfg[0] if cfg else (dotenv_values(ENV_FILE).get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set in .env")
    try:
        body = requests.get(API.format(token=token, method="getUpdates"), timeout=10).json()
    except (requests.RequestException, ValueError) as e:  # keep the token-bearing URL out of the output
        raise SystemExit(f"getUpdates failed: {type(e).__name__}") from None
    if not body.get("ok"):
        raise SystemExit(f"getUpdates failed: {body.get('description', 'unknown error')}")
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("username") or u["message"]["chat"].get("title")
             for u in body.get("result", []) if "message" in u}
    if not chats:
        print("no messages yet: send your bot any message in Telegram, then run this again")
    for cid, name in chats.items():
        print(f"TELEGRAM_CHAT_ID={cid}   ({name})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chat-ids", action="store_true")
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if a.chat_ids:
        _chat_ids()
    elif a.test:
        print("sent" if send("테스트 알림입니다.") else "not sent (check TELEGRAM_* in .env and logs)")
    else:
        ap.print_help()
