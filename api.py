"""Versioned settings API and the existing investment report."""
from __future__ import annotations

import hmac
import os
import sqlite3
import threading
from pathlib import Path
from typing import Annotated, Literal

import pandas as pd
from dotenv import dotenv_values
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, WithJsonSchema

import control
import db
import webreport
from markets import MARKETS, current_session

ENV_FILE = Path(__file__).with_name(".env")
_calendar_lock = threading.Lock()


class Trading(BaseModel):
    broker: Literal["sim", "kis"]
    submit: bool
    allow_real: bool


class Paused(BaseModel):
    KR: bool
    US: bool


class Limit(BaseModel):
    type: Literal["int", "float"]
    min: float
    max: float


class MarketTimes(BaseModel):
    session: str | None
    signal: str | None
    execute: str | None
    close: str | None


class Run(BaseModel):
    job: str
    market: str
    session_date: str
    started_at: str
    finished_at: str | None
    status: str
    detail: str | None


class Status(BaseModel):
    trading: Trading
    paused: Paused
    markets: dict[str, MarketTimes]
    runs: list[Run]
    db_version: str


class Snapshot(BaseModel):
    trading: Trading
    paused: Paused
    config: dict[str, float | int | str]
    editable: list[str]


class Settings(Snapshot):
    limits: dict[str, Limit]


class LogEntry(BaseModel):
    id: int
    at: str
    key: str
    old: str | None
    new: str | None
    source: str


class Order(BaseModel):
    id: int
    session_date: str
    market: str
    ticker: str
    side: str
    step: int
    qty: int
    ref_price: float
    limit_price: float
    filled_qty: int
    fill_price: float | None
    cost: float | None
    status: str
    broker_order_no: str | None
    created_at: str
    error: str | None


# The schema exposes the allowed brokers; control.update validates their values.
BrokerPatch = Annotated[StrictStr, WithJsonSchema({"type": "string", "enum": ["sim", "kis"]})]


class TradingPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    broker: BrokerPatch | None = None
    submit: StrictBool | None = None
    allow_real: StrictBool | None = None


class PausedPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    KR: StrictBool | None = None
    US: StrictBool | None = None


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    trading: TradingPatch | None = None
    paused: PausedPatch | None = None
    config: dict | None = None
    confirm: str | None = None


def load_token(path: Path = ENV_FILE) -> str | None:
    values = {**dotenv_values(path), **{k: v for k, v in os.environ.items() if k.startswith("MCR_")}}
    return (values.get("MCR_API_TOKEN") or "").strip() or None


def create_app(db_path: Path | str, token: str | None,
               web_dir: Path = Path(__file__).with_name("frontend") / "dist",
               db_timeout: float = 3.0) -> FastAPI:
    db.connect(db_path).close()
    app = FastAPI(title="market-cap-rebalancer", docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    bearer = HTTPBearer(auto_error=False)

    def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if not token or not token.strip():
            raise HTTPException(status_code=503, detail="API token not configured")
        if credentials is None or not hmac.compare_digest(credentials.credentials.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Bearer"})

    def connection():
        conn = sqlite3.connect(db_path, timeout=db_timeout, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request: Request, exc: sqlite3.OperationalError):
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return JSONResponse(status_code=503, content={"detail": "DB busy, retry"})
        raise exc

    @app.exception_handler(RequestValidationError)
    async def request_error(request: Request, exc: RequestValidationError):
        if request.method == "PATCH" and request.url.path == "/api/v1/settings":
            for error in exc.errors():
                loc = error["loc"]
                if len(loc) == 3 and loc[0] == "body" and loc[1] in ("trading", "paused"):
                    key = loc[2]
                    if loc[1] == "paused":
                        detail = f"일시정지 설정이 올바르지 않습니다: {key}"
                    elif key == "broker":
                        detail = "broker는 sim 또는 kis여야 합니다"
                    else:
                        detail = f"{key} 값은 참 또는 거짓이어야 합니다"
                    return JSONResponse(status_code=400, content={"detail": detail})
        return await request_validation_exception_handler(request, exc)

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])

    @router.get("/status", response_model=Status)
    def status(conn: sqlite3.Connection = Depends(connection)):
        now = pd.Timestamp.now(tz="UTC")
        market_status = {}
        with _calendar_lock:
            for code, market in MARKETS.items():
                session = current_session(market, now)
                market_status[code] = {
                    "session": session.session if session else None,
                    **{key: getattr(session, key).tz_convert("UTC").isoformat() if session else None
                       for key in ("signal", "execute", "close")},
                }
        runs = [dict(row) for row in conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 10")]
        return {"trading": control.trading_settings(conn), "paused": control.paused(conn),
                "markets": market_status, "runs": runs, "db_version": webreport.db_version(db_path)}

    @router.get("/settings", response_model=Settings)
    def get_settings(conn: sqlite3.Connection = Depends(connection)):
        return control.snapshot(conn) | {"limits": control.LIMITS}

    @router.patch("/settings", response_model=Snapshot)
    def patch_settings(patch: SettingsPatch, conn: sqlite3.Connection = Depends(connection)):
        changes = patch.model_dump(exclude_unset=True)
        confirm = changes.pop("confirm", None)
        try:
            return control.update(conn, changes, "web", confirm=confirm)
        except control.ControlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/settings/log", response_model=list[LogEntry])
    def settings_log(limit: int = Query(default=50, ge=1), conn: sqlite3.Connection = Depends(connection)):
        return db.setting_changes(conn, limit)

    @router.get("/orders", response_model=list[Order])
    def orders(limit: int = Query(default=40, ge=1), conn: sqlite3.Connection = Depends(connection)):
        return [dict(row) for row in conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,))]

    app.include_router(router)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/index.html", response_class=HTMLResponse, include_in_schema=False)
    def report_page():
        return HTMLResponse(webreport.render(db_path), headers={"Cache-Control": "no-store"})

    @app.get("/version", response_class=PlainTextResponse, include_in_schema=False)
    def version():
        return PlainTextResponse(webreport.db_version(db_path), headers={"Cache-Control": "no-store"})

    if web_dir.is_dir():
        app.mount("/app", StaticFiles(directory=web_dir, html=True), name="app")
    return app
