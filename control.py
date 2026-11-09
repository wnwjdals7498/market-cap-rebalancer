"""Validation and atomic updates for runtime settings."""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict

import db
import notify
from strategy import Config


class ControlError(ValueError):
    pass


TRADING_DEFAULTS = {"broker": "sim", "submit": False, "allow_real": False}
PAUSED_DEFAULTS = {"KR": False, "US": False}
LIMITS = {
    "stock_pct": {"type": "float", "min": 0, "max": 100},
    "us_pct": {"type": "float", "min": 0, "max": 100},
    "band_pp": {"type": "float", "min": 0, "max": 50},
    "step_pct": {"type": "float", "min": 1, "max": 100},
    "confirm_days": {"type": "int", "min": 0, "max": 30},
    "pair_confirm_days": {"type": "int", "min": 0, "max": 30},
    "solo_after_days": {"type": "int", "min": 0, "max": 365},
    "min_trade_pct": {"type": "float", "min": 0, "max": 10},
    "us_fee": {"type": "float", "min": 0, "max": 0.05},
    "kr_fee": {"type": "float", "min": 0, "max": 0.05},
}


def trading_settings(conn: sqlite3.Connection) -> dict:
    return TRADING_DEFAULTS | db.get_setting(conn, "trading", {})


def sends_orders(t: dict) -> bool:
    return t["broker"] == "kis" and bool(t["submit"])


def paused(conn: sqlite3.Connection) -> dict[str, bool]:
    return PAUSED_DEFAULTS | db.get_setting(conn, "paused", {})


def snapshot(conn: sqlite3.Connection) -> dict:
    config = asdict(Config())
    del config["kr_taxable"]
    saved = db.get_setting(conn, "config", {})
    config.update({key: value for key, value in saved.items() if key in config})
    return {"trading": trading_settings(conn), "paused": paused(conn),
            "config": config, "editable": sorted(LIMITS)}


def update(conn: sqlite3.Connection, changes: dict, source: str, confirm: str | None = None) -> dict:
    if source not in ("web", "cli"):
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
                limit = LIMITS[key]
                if type(value) not in (int, float) or (limit["type"] == "int" and type(value) is not int):
                    raise ControlError(f"숫자 형식이 올바르지 않습니다: {key}")
                if not limit["min"] <= value <= limit["max"] or not math.isfinite(value):
                    raise ControlError(f"허용 범위를 벗어난 값입니다: {key}")
                if limit["type"] == "float":
                    value = float(value)
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
