"""Versioned settings API and the existing investment report."""
from __future__ import annotations

import hmac
import os
import sqlite3
import threading
from pathlib import Path

import pandas as pd
from dotenv import dotenv_values
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

import control
import db
import webreport
from markets import MARKETS, current_session

ENV_FILE = Path(__file__).with_name(".env")
_calendar_lock = threading.Lock()


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    trading: dict | None = None
    paused: dict | None = None
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

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])

    @router.get("/status")
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

    @router.get("/settings")
    def get_settings(conn: sqlite3.Connection = Depends(connection)):
        return control.snapshot(conn) | {"limits": control.LIMITS}

    @router.patch("/settings")
    def patch_settings(patch: SettingsPatch, conn: sqlite3.Connection = Depends(connection)):
        changes = patch.model_dump(exclude_unset=True)
        confirm = changes.pop("confirm", None)
        try:
            return control.update(conn, changes, "web", confirm=confirm)
        except control.ControlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/settings/log")
    def settings_log(limit: int = Query(default=50, ge=1), conn: sqlite3.Connection = Depends(connection)):
        return db.setting_changes(conn, limit)

    @router.get("/orders")
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
