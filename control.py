"""Validation and atomic updates for runtime settings."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import asdict
from typing import Any

import db
import notify
from strategy import Config


class ControlError(ValueError):
    pass


TRADING_DEFAULTS = {"broker": "sim", "submit": False, "allow_real": False}
PAUSED_DEFAULTS = {"KR": False, "US": False}
US_TICKER = r"^[A-Z][A-Z.\-]{0,5}$"
KR_TICKER = r"^\d{6}\.KS$"
SOURCES = ("web", "cli", "system", "migration")
LIMITS: dict[str, dict[str, Any]] = {
    "stock_pct": {"type": "float", "label": "주식 비중 (%)", "min": 0, "max": 100},
    "us_pct": {"type": "float", "label": "미국 포켓 비중 (%)", "min": 0, "max": 100},
    "band_pp": {"type": "float", "label": "주식/채권 허용 오차 (%p)", "min": 0, "max": 50},
    "step_pct": {"type": "float", "label": "매매 1회 최대 이동량 (포켓 대비 %)", "min": 1, "max": 100},
    "confirm_days": {"type": "int", "label": "순위 변경 대기일", "min": 0, "max": 30},
    "min_trade_pct": {"type": "float", "label": "최소 주문 크기 (포켓 대비 %)", "min": 0, "max": 10},
    "us_fee": {"type": "float", "label": "미국 수수료율", "min": 0, "max": 0.05},
    "kr_fee": {"type": "float", "label": "한국 수수료율", "min": 0, "max": 0.05},
    "rank_weights": {"type": "weights", "label": "순위별 주식 비율 (1~5위, 합 100)", "min": 0, "max": 100},
    "short_bond_pct": {"type": "float", "label": "채권 중 단기채 비율 (%)", "min": 0, "max": 100},
    "trade_every": {"type": "choice", "label": "매매 주기", "options": ["day", "week", "month"]},
    "fx_band_pp": {"type": "float", "label": "환전 허용 오차 (%p)", "min": 0, "max": 50},
    "fx_enabled": {"type": "bool", "label": "환전 안내 사용"},
    "fx_wait_days": {"type": "int", "label": "환전 대기 일수", "min": 1, "max": 30},
    "us_universe": {"type": "tickers", "label": "미국 주식 후보 (5종목)", "market": "US"},
    "kr_universe": {"type": "tickers", "label": "한국 주식 후보 (5종목)", "market": "KR"},
    "us_short_bond": {"type": "ticker", "label": "미국 단기채", "market": "US"},
    "us_long_bond": {"type": "ticker", "label": "미국 장기채", "market": "US"},
    "kr_short_bond": {"type": "ticker", "label": "한국 단기채", "market": "KR"},
    "kr_long_bond": {"type": "ticker", "label": "한국 장기채", "market": "KR"},
}
RANK_SLOTS = 5
# Settings the 2-stage strategy replaced; `migrate` drops them from a stored config.
OLD_KEYS = ("pair_confirm_days", "solo_after_days", "us_bond", "kr_bond", "fx_mode")
OLD_STOCKS = {
    "US": ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA"),
    "KR": ("005930.KS", "000660.KS", "373220.KS", "207940.KS", "005380.KS"),
}
OLD_BONDS = {"US": "IEF", "KR": "148070.KS"}
CONFIG_VERSION = 2


def is_number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def validate_config(key: str, value):
    """Check one config value against its LIMITS entry and return the value to store."""
    limit = LIMITS[key]
    kind = limit["type"]
    if kind in ("int", "float"):
        if type(value) not in (int, float) or (kind == "int" and type(value) is not int):
            raise ControlError(f"숫자 형식이 올바르지 않습니다: {key}")
        if not math.isfinite(value) or not limit["min"] <= value <= limit["max"]:
            raise ControlError(f"허용 범위를 벗어난 값입니다: {key}")
        return float(value) if kind == "float" else value
    if kind == "bool":
        if type(value) is not bool:
            raise ControlError(f"{key} 값은 참 또는 거짓이어야 합니다")
        return value
    if kind == "choice":
        if value not in limit["options"] or type(value) is not str:
            raise ControlError(f"{key} 값은 {'/'.join(limit['options'])} 중 하나여야 합니다")
        return value
    if kind == "weights":
        if not isinstance(value, list) or len(value) != RANK_SLOTS or not all(is_number(w) for w in value):
            raise ControlError(f"{key} 는 숫자 {RANK_SLOTS}개여야 합니다")
        if any(not limit["min"] <= w <= limit["max"] for w in value):
            raise ControlError(f"허용 범위를 벗어난 값입니다: {key}")
        if abs(sum(value) - 100) > 1e-6:
            raise ControlError(f"{key} 의 합은 100이어야 합니다")
        return [float(w) for w in value]
    pattern = US_TICKER if limit["market"] == "US" else KR_TICKER
    names = value if kind == "tickers" else [value]
    if kind == "tickers" and (not isinstance(value, list) or len(value) != RANK_SLOTS):
        raise ControlError(f"{key} 는 종목 {RANK_SLOTS}개여야 합니다")
    if not all(isinstance(t, str) and re.fullmatch(pattern, t) for t in names):
        raise ControlError(f"종목 코드 형식이 올바르지 않습니다: {key}")
    if len(set(names)) != len(names):
        raise ControlError(f"중복된 종목이 있습니다: {key}")
    return value


def roles(config: dict, market: str) -> dict[str, str]:
    """Managed tickers of one market with their role (stock or bond)."""
    prefix = market.lower()
    out = {t: "stock" for t in config[f"{prefix}_universe"]}
    out |= {config[f"{prefix}_short_bond"]: "bond", config[f"{prefix}_long_bond"]: "bond"}
    return out


def check_universe(config: dict) -> None:
    for market in ("US", "KR"):
        prefix = market.lower()
        short, long = config[f"{prefix}_short_bond"], config[f"{prefix}_long_bond"]
        if short == long:
            raise ControlError(f"{market} 단기채와 장기채는 서로 달라야 합니다")
        if {short, long} & set(config[f"{prefix}_universe"]):
            raise ControlError(f"{market} 채권은 주식 후보와 겹칠 수 없습니다")


def retire_removed(conn: sqlite3.Connection, before: dict, after: dict) -> None:
    """Keep tickers dropped from the candidate/bond lists so the strategy can sell them down."""
    retired = db.get_setting(conn, "retired", {})
    changed = False
    for market in ("US", "KR"):
        old, new = roles(before, market), roles(after, market)
        current = retired.get(market, {})
        gone = {t: role for t, role in old.items() if t not in new and t not in current}
        back = [t for t in current if t in new]
        if gone or back:
            retired[market] = {t: role for t, role in current.items() if t not in new} | gone
            changed = True
    if changed:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", ("retired", json.dumps(retired)))


def drop_retired(conn: sqlite3.Connection, market: str, tickers: list[str], source: str = "system") -> None:
    """Remove fully sold tickers from the retired list of one market."""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        retired = db.get_setting(conn, "retired", {})
        current = retired.get(market, {})
        left = {t: role for t, role in current.items() if t not in tickers}
        if left == current:
            return
        retired[market] = left
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", ("retired", json.dumps(retired)))
        db.log_setting_change(conn, f"retired.{market}", current, left, source)
    notify.send(f"🧹 {market} 정리 완료: " + ", ".join(sorted(set(current) - set(left))))


def trading_settings(conn: sqlite3.Connection) -> dict:
    return TRADING_DEFAULTS | db.get_setting(conn, "trading", {})


def sends_orders(t: dict) -> bool:
    return t["broker"] == "kis" and bool(t["submit"])


def paused(conn: sqlite3.Connection) -> dict[str, bool]:
    return PAUSED_DEFAULTS | db.get_setting(conn, "paused", {})


def snapshot(conn: sqlite3.Connection) -> dict:
    config = {key: list(value) if isinstance(value, tuple) else value
              for key, value in asdict(Config()).items() if key != "kr_taxable"}
    saved = db.get_setting(conn, "config", {})
    config.update({key: value for key, value in saved.items() if key in config})
    return {"trading": trading_settings(conn), "paused": paused(conn),
            "config": config, "editable": sorted(LIMITS)}


def update(conn: sqlite3.Connection, changes: dict, source: str, confirm: str | None = None) -> dict:
    if source not in SOURCES:
        raise ControlError("변경 출처가 올바르지 않습니다")
    if not isinstance(changes, dict) or any(k not in ("trading", "paused", "config") for k in changes):
        raise ControlError("설정 항목이 올바르지 않습니다")
    normalized: dict[str, dict] = {}
    for group, values in changes.items():
        if not isinstance(values, dict):
            raise ControlError(f"{group} 설정은 객체여야 합니다")
        normalized[group] = {}
        for key, value in values.items():
            if group == "trading":
                if key == "broker":
                    if value not in ("sim", "kis"):
                        raise ControlError("broker는 sim 또는 kis여야 합니다")
                elif key in ("submit", "allow_real"):
                    if type(value) is not bool:
                        raise ControlError(f"{key} 값은 참 또는 거짓이어야 합니다")
                    if key == "allow_real" and source != "cli":
                        raise ControlError("allow_real은 CLI에서만 변경할 수 있습니다")
                else:
                    raise ControlError(f"변경할 수 없는 항목: {group}.{key}")
            elif group == "paused":
                if key not in PAUSED_DEFAULTS or type(value) is not bool:
                    raise ControlError(f"일시정지 설정이 올바르지 않습니다: {key}")
            else:
                if key not in LIMITS:
                    raise ControlError(f"변경할 수 없는 항목: {group}.{key}")
                value = validate_config(key, value)
            normalized[group][key] = value

    recorded = []
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        before = snapshot(conn)
        old_trading = before["trading"]
        new_trading = old_trading | normalized.get("trading", {})
        submit_enabled = not old_trading["submit"] and new_trading["submit"]
        sending_started = not sends_orders(old_trading) and sends_orders(new_trading)
        if source == "web" and (submit_enabled or sending_started) and confirm != "SUBMIT":
            raise ControlError("주문 전송을 켜려면 SUBMIT 확인이 필요합니다")
        after_config = before["config"] | normalized.get("config", {})
        check_universe(after_config)
        retire_removed(conn, before["config"], after_config)
        for group, values in normalized.items():
            saved = db.get_setting(conn, group, {})
            changed = False
            for key, value in values.items():
                old = before[group][key]
                if old != value:
                    saved[key] = value
                    db.log_setting_change(conn, f"{group}.{key}", old, value, source)
                    recorded.append(f"{group}.{key} {old} → {value}")
                    changed = True
            if changed:
                conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (group, json.dumps(saved)))
        result = snapshot(conn)
    if recorded:
        notify.send(f"⚙️ 설정 변경 ({source}): " + ", ".join(recorded))
    return result


def migrate(conn: sqlite3.Connection) -> bool:
    """Move a stored pre-rank-weights config to version 2 once; return True if anything changed."""
    if db.get_setting(conn, "config:version", 0) >= CONFIG_VERSION:
        return False
    saved = db.get_setting(conn, "config", {})
    if not any(key in saved for key in OLD_KEYS):  # new DB: nothing to carry over
        db.set_setting(conn, "config:version", CONFIG_VERSION)
        return False
    recorded = []
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if db.get_setting(conn, "config:version", 0) >= CONFIG_VERSION:  # another process migrated first
            return False
        saved = db.get_setting(conn, "config", {})
        defaults = asdict(Config())
        effective = snapshot(conn)["config"]
        migrated = {key: value for key, value in saved.items() if key not in OLD_KEYS}
        migrated |= {"confirm_days": 0, "us_pct": 60.0}
        for key in OLD_KEYS:
            if key in saved:
                db.log_setting_change(conn, f"config.{key}", saved[key], None, "migration")
                recorded.append(f"config.{key} 삭제")
        for key in ("confirm_days", "us_pct"):
            old = saved.get(key, defaults[key])
            if old != migrated[key]:
                db.log_setting_change(conn, f"config.{key}", old, migrated[key], "migration")
                recorded.append(f"config.{key} {old} → {migrated[key]}")
        retired = db.get_setting(conn, "retired", {})
        for market in ("US", "KR"):
            old = {t: "stock" for t in OLD_STOCKS[market]}
            old |= {saved.get(f"{market.lower()}_bond", OLD_BONDS[market]): "bond", OLD_BONDS[market]: "bond"}
            new = roles(effective, market)
            retired[market] = retired.get(market, {}) | {t: r for t, r in old.items() if t not in new}
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", ("config", json.dumps(migrated)))
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", ("retired", json.dumps(retired)))
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", ("config:version", json.dumps(CONFIG_VERSION)))
    notify.send("⚙️ 설정 이관 (migration): " + ", ".join(recorded) + f" | 정리 대상 {retired}")
    return True
