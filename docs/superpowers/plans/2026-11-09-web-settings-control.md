# 웹 설정 제어 (API 백엔드 + 웹 UI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `jobs.py config` 같은 CLI 대신 브라우저에서 운용 설정(브로커, 주문 전송, 시장별 일시정지)과 전략 파라미터를 보고 바꿀 수 있게 하고, 같은 API 를 나중에 모바일 앱이 그대로 쓰게 한다.

**Architecture:** 설정 읽기·검증·변경·이력·알림을 `control.py` 한 곳에 모으고, CLI(`jobs.py config`)와 HTTP API(`api.py`, FastAPI)가 둘 다 이 함수를 쓴다 (백테스트와 실운용이 `strategy.py` 를 같이 쓰는 것과 같은 원칙). 매매 실행은 지금처럼 systemd 타이머의 `jobs.py tick` 이 하고, 웹은 SQLite `settings` 만 바꾼다 — 웹 서버가 죽어도 매매는 돈다. 웹 UI 는 React + Vite + TypeScript SPA(`frontend/`)로 API 의 한 클라이언트이고, 타입은 OpenAPI 에서 생성한다. 앱은 나중에 같은 `/api/v1` 과 같은 생성 타입을 쓰는 다른 클라이언트(React Native 등)가 된다.

**Tech Stack:** Python 3.10+ (서버 3.12, PC 3.13), FastAPI, uvicorn, SQLite / Node 20.19+ 또는 22.12+, React 19, Vite 8, TypeScript 5.9, Vitest, ESLint, openapi-typescript

**Spec:** 이 문서의 "설계" 절 (사용자가 질문 단계를 건너뛰어 별도 spec 없음. 사용자 요구: "설정 컨트롤 방식이 손에 익지 않아 웹으로 먼저 만들고, 나중에 앱으로 확장할 여지를 둔다")

---

## 설계

### 전제 정정

- 현재 자동 실행은 crontab 이 아니라 systemd 타이머(`mcr-tick.timer`, 5분)다. crontab 은 `deploy/DEPLOY.md` 의 DB 백업 예시뿐이다.
- 설정은 이미 SQLite `settings` 테이블(JSON)에 있다: `trading`(broker/submit/allow_real), `config`(전략 `Config` 덮어쓰기). tick 은 매 실행마다 이 값을 다시 읽으므로, 웹에서 바꾼 값은 **다음 tick(최대 5분 뒤)** 부터 적용된다. 별도 재시작이 필요 없다.

### 목표

1. 웹에서 볼 수 있다: 설정 전체, 시장별 오늘 signal/execute 시각, 최근 실행(runs), 최근 주문, 설정 변경 이력.
2. 웹에서 바꿀 수 있다: broker(sim/kis), 주문 전송(submit), 시장별 일시정지(KR/US), 전략 파라미터 중 편집 허용 필드.
3. 모든 변경은 이력(`settings_log`)에 남고 텔레그램으로 알린다. CLI 변경도 같다.
4. API 는 `/api/v1` 로 버전을 고정하고 OpenAPI(`/api/openapi.json`)를 내준다 — 앱 클라이언트를 이 스키마로 만든다.

### 비목표 (이번 범위 밖)

- 모바일 앱 자체, 원격 접속(Tailscale·리버스 프록시·HTTPS), 기기별 토큰, 사용자 계정.
- systemd 타이머 대체. 실행 주기 변경 UI.
- 전략 규칙(코드) 변경, 백테스트 실행 UI.

### 반목표 (동작해도 실패로 본다)

- 웹 클릭 하나로 실전 매매가 켜지는 것: `allow_real` 은 웹에서 **읽기 전용**, CLI(`jobs.py config --allow-real`)로만 바꾼다.
- "지금 실행" 버튼처럼 `runs` 테이블의 1세션 1회 보장을 우회하는 기능.
- 웹 서버가 sudo·systemctl 을 부르는 것. 일시정지는 타이머를 멈추지 않고 설정값으로 한다.
- 토큰을 코드·SQLite·로그·커밋에 넣는 것. 인증 없이 `0.0.0.0` 이나 공인 IP 로 여는 것.

### 결정 사항

| 항목 | 결정 | 근거 |
|---|---|---|
| 백엔드 | FastAPI + uvicorn | OpenAPI 자동 생성 → 앱 클라이언트 생성에 그대로 사용. 요청 검증(pydantic) 내장 |
| 인증 | `.env` 의 `MCR_API_TOKEN` 하나, `Authorization: Bearer` 헤더, `hmac.compare_digest` 비교. 토큰 미설정이면 `/api/v1/*` 전부 503 (닫힌 상태로 실패) | 앱도 같은 헤더를 쓴다. 쿠키가 아니라 CSRF(사이트 간 요청 위조) 대상이 아니다 |
| 네트워크 | 기본 `127.0.0.1:8765` 그대로, SSH 터널로 접속 | 지금 운영 방식 유지. 원격 공개는 앱 단계에서 따로 결정 |
| 서비스 | 기존 `mcr-web.service` 의 명령 `jobs.py web --serve --host --port` 를 그대로 두고 내부만 uvicorn 으로 교체 | systemd 유닛·Windows 스크립트를 바꿀 필요가 없다 |
| 기존 리포트 | `GET /`, `GET /version` 은 지금과 같은 응답(인증 없음) | 이미 쓰는 화면 유지. 원격 공개 단계에서 인증 뒤로 옮긴다 |
| 일시정지 | `settings["paused"] = {"KR": bool, "US": bool}`. tick 이 해당 시장 signal/execute 를 건너뛴다. 일일 보고는 계속 | 서버 설정 변경 없이 멈춤/재개 |
| 편집 가능한 전략 필드 | `stock_pct, us_pct, band_pp, step_pct, confirm_days, pair_confirm_days, solo_after_days, min_trade_pct, us_fee, kr_fee` | 나머지(`fx_mode, us_bond, kr_bond, kr_stock_sell_tax, fx_spread`)는 바꾸면 보유 종목·모드가 바뀌어 매매가 생기므로 읽기 전용 [작성자 판단] |
| 주문 전송 켜기 | 웹에서 `submit` 을 false→true 로 바꿀 때 요청에 `"confirm": "SUBMIT"` 필수 | 실수 클릭 방지 |
| 프런트엔드 | 선택지 B: React + Vite + TypeScript (사용자 결정 2026-11-09) | 앱(React Native)과 컴포넌트·타입 공유 여지 |

**프런트엔드 선택지** (결정: B)

| 선택지 | 방식 | 장점 | 단점 | 적합한 조건 |
|---|---|---|---|---|
| A | 빌드 없는 정적 HTML/JS (`web/`), API 가 `/app/` 으로 서빙 | 저장소가 Python 하나로 유지, PC·서버에 Node 불필요, 기존 리포트와 같은 방식 | 화면이 커지면 코드 정리가 어려움 | 화면이 설정·상태 몇 개 수준일 때 |
| B | React + Vite + TypeScript SPA | 컴포넌트·타입을 React Native 앱과 공유 가능 | Node 빌드 도구가 PC·서버 양쪽에 필요, 의존성 증가 | 앱도 React Native 로 만들기로 이미 정했을 때 |
| C | A + PWA(홈 화면 설치 가능한 웹앱) 매니페스트 | 앱 스토어 없이 휴대폰에 "앱"처럼 설치 | 서비스 워커는 HTTPS 또는 localhost 에서만 동작 → 원격 HTTPS 단계가 먼저 필요 | 원격 접속을 열 때 함께 |

B 를 쓰는 대신 생기는 운영 조건: 서버·PC 에 Node 가 필요하고, 배포 때 `frontend/` 에서 `npm ci && npm run build` 를 한다. 빌드 결과 `frontend/dist/` 는 git 에 넣지 않는다(생성물). 실행 중에는 Node 가 필요 없다 — FastAPI 가 `frontend/dist/` 를 정적 파일로 서빙한다.

### API (v1)

| 메서드·경로 | 인증 | 응답 |
|---|---|---|
| `GET /api/v1/status` | 필요 | `{"trading": {...}, "paused": {...}, "markets": {"KR": {"session": "YYYY-MM-DD"|null, "signal": ISO|null, "execute": ISO|null, "close": ISO|null}, "US": {...}}, "runs": [최근 10건], "db_version": str}` |
| `GET /api/v1/settings` | 필요 | `control.snapshot()` + `"limits"` (편집 필드별 `{"type","min","max"}`) |
| `PATCH /api/v1/settings` | 필요 | 본문 `{"trading"?: {...}, "paused"?: {...}, "config"?: {...}, "confirm"?: str}` → 200 새 snapshot / 400 `{"detail": 사유}` / 503 DB 잠김 |
| `GET /api/v1/settings/log?limit=50` | 필요 | 변경 이력 최신순 |
| `GET /api/v1/orders?limit=40` | 필요 | 주문 최신순 (`orders` 테이블 행) |
| `GET /`, `GET /version` | 없음 | 기존 리포트 HTML, `db_version` |
| `GET /app/` | 없음 (정적 파일, 데이터는 API 로만) | 설정 UI (`frontend/dist/`, 없으면 마운트 안 함) |
| `GET /api/openapi.json`, `GET /api/docs` | 없음 | 스키마·Swagger UI. `redoc_url=None` |

---

## Global Constraints

- 프런트엔드 의존성 (2026-11-09 `npm view` 확인, peer 호환 반영): `react`/`react-dom` ^19.3, `vite` ^8.3, `@vitejs/plugin-react` ^6.3, `typescript` **~5.9.3** (typescript-eslint 8.72 의 peer 가 `<6.1.0`, openapi-typescript 7.13 의 peer 가 `^5.x` 라 TS 7 금지), `vitest` ^5.1, `jsdom`, `@testing-library/react` ^16.3, `@testing-library/jest-dom`, `eslint` ^10, `typescript-eslint` ^8.72, `eslint-plugin-react-hooks`, `openapi-typescript` ^7.13. Node `^20.19.0 || >=22.12.0` (Vite 8 engines). 서버 Node v24.22.2 확인.
- 프런트엔드 검증 명령은 `frontend/` 에서 `npm run lint`, `npm run typecheck`(`tsc -b --noEmit` 또는 템플릿의 `tsc -b`), `npm test -- --run`, `npm run build` 네 개다. 넷 다 통과해야 커밋한다.
- Python 3.10 이상. 새 의존성: `fastapi>=0.162`, `uvicorn>=0.54`, `httpx>=0.28` (TestClient 용). PyPI 최신 확인값(2026-11-09 `pip index versions`): fastapi 0.162.2, uvicorn 0.54.0, httpx 0.28.1. `requirements.txt` 에 추가하고 `.venv` 에만 설치한다.
- `jobs.py web --serve --host 127.0.0.1 --port 8765` 명령 형식을 바꾸지 않는다 (`deploy/systemd/mcr-web.service`, `scripts/register_web.ps1` 이 이 형식을 쓴다).
- 토큰은 `.env` 의 `MCR_API_TOKEN` 에서만 읽는다 (`dotenv_values(ENV_FILE)` + `MCR_` 로 시작하는 환경변수 우선, `kis_auth.load_credentials` 와 같은 방식). 로그·응답·SQLite 에 쓰지 않는다.
- 텔레그램은 반드시 `notify.send(...)` 로 모듈 속성을 통해 부른다 (`tests/conftest.py` 가 `notify.send` 를 바꿔 끼워 실제 전송을 막는다).
- 테스트는 네트워크를 쓰지 않는다. 수동 실행은 `jobs.py --db <임시경로>` 로만 한다. 실사용 `mcr.db` 를 건드리지 않는다.
- 설정 필드 설명 주석은 한국어, docstring 은 영어 (AGENTS.md).
- 작업(Task) 하나 = 커밋 하나. 커밋 전 `.venv/bin/python -m pytest -q` 통과(Windows 는 `.venv/Scripts/python`). push 는 사용자 요청 때만.
- 설정 변경은 다음 tick 부터 적용된다. 실행 중(RUNNING)인 작업을 멈추거나 `runs` 를 건드리지 않는다.

## Review Focus

1. JSON `true`/`false` 를 숫자 필드(`confirm_days` 등)에 넣는 요청 — Python 에서 `bool` 은 `int` 의 하위 타입이라 그냥 통과하기 쉽다. 400 이어야 한다. NaN·Infinity 도 400. (Task 1 테스트)
2. 한 요청에 정상 값과 잘못된 값이 섞인 경우 — 아무것도 저장되지 않아야 한다(전부 아니면 전무). `submit` 켜기에 `confirm` 이 없을 때 같은 요청의 다른 필드도 저장되면 안 된다. (Task 1 테스트)
3. tick 이 쓰기 중이라 SQLite 가 잠긴 상태에서 PATCH — 500 이나 무한 대기가 아니라 짧은 대기 뒤 503 "DB busy, retry". (Task 3 테스트)
4. `.env` 에 `MCR_API_TOKEN` 이 없거나 빈 값 — API 는 503 으로 닫히고, 기존 리포트 `/` 는 계속 보인다. (Task 3 테스트)
5. 한 시장만 일시정지 — 그 시장 signal/execute 만 건너뛰고 다른 시장과 일일 보고는 그대로 돈다. (Task 2 테스트)

---

## File Structure

| 파일 | 상태 | 책임 |
|---|---|---|
| `control.py` | 새로 | 설정 snapshot, 편집 규칙(`LIMITS`), 검증, 원자적 변경, 이력 기록, 텔레그램 알림 |
| `db.py` | 수정 | `settings_log` 테이블, `log_setting_change`, `setting_changes` |
| `jobs.py` | 수정 | `config` 명령이 `control.update` 사용, `--pause/--resume`, tick 의 일시정지, `web --serve` 가 uvicorn 실행 |
| `api.py` | 새로 | `create_app(db_path, token)`, 인증, `/api/v1/*`, 기존 리포트·정적 UI 마운트 |
| `webreport.py` | 수정 | `make_server`/`serve` 삭제 (api.py 로 대체). `render`, `db_version`, `build_html` 유지 |
| `frontend/` (Vite react-ts 템플릿 기반) | 새로 | 설정 UI. `src/api/schema.d.ts`(openapi-typescript 생성물, 커밋함), `src/api/client.ts`(fetch + Bearer), `src/App.tsx` 와 섹션 컴포넌트, `src/**/*.test.tsx` |
| `scripts/export_openapi.py` | 새로 | `api.create_app(...).openapi()` 를 `frontend/openapi.json` 으로 저장 (서버 실행 없이 타입 생성) |
| `.gitignore` | 수정 | `frontend/node_modules/`, `frontend/dist/` |
| `tests/test_control.py`, `tests/test_api.py` | 새로 | |
| `tests/test_jobs.py` | 수정 | 일시정지 tick 테스트, `test_served_page_tracks_db_changes` 를 API 기반으로 이동 |
| `requirements.txt`, `.env.example`, `README.md`, `deploy/DEPLOY.md`, `AGENTS.md` | 수정 | 의존성, 토큰 키, 사용법(Node·빌드 포함), 코드 구조 표 |

---

### Task 1: 설정 서비스와 변경 이력 (`control.py`)

**Files:**
- Create: `control.py`
- Modify: `db.py` (SCHEMA 에 `settings_log`, 접근 함수 2개), `jobs.py:358-360` (`TRADING_DEFAULTS`, `trading_settings` 를 control 로 이동), `jobs.py:460-464` (`config` 명령)
- Test: `tests/test_control.py`

**Interfaces:**
- Produces (`db.py`):
  - 테이블 `settings_log(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, key TEXT NOT NULL, old TEXT, new TEXT, source TEXT NOT NULL)` — `key` 는 `"trading.submit"`, `"paused.KR"`, `"config.stock_pct"` 처럼 점 경로, `old`/`new` 는 JSON.
  - `log_setting_change(conn, key: str, old, new, source: str) -> None` (트랜잭션은 호출자가 연다)
  - `setting_changes(conn, limit: int = 50) -> list[dict]` 최신순
- Produces (`control.py`):
  - `class ControlError(ValueError)` — 사용자에게 보여 줄 한국어 사유
  - `TRADING_DEFAULTS = {"broker": "sim", "submit": False, "allow_real": False}`, `PAUSED_DEFAULTS = {"KR": False, "US": False}`
  - `LIMITS: dict[str, dict]` — 편집 가능 Config 필드 → `{"type": "float"|"int", "min": x, "max": y}`. 값: `stock_pct` float 0–100, `us_pct` float 0–100, `band_pp` float 0–50, `step_pct` float 1–100, `confirm_days` int 0–30, `pair_confirm_days` int 0–30, `solo_after_days` int 0–365, `min_trade_pct` float 0–10, `us_fee` float 0–0.05, `kr_fee` float 0–0.05 [작성자 판단]
  - `trading_settings(conn) -> dict`, `paused(conn) -> dict[str, bool]`
  - `snapshot(conn) -> dict` → `{"trading", "paused", "config": Config 전체 필드(kr_taxable 제외), "editable": sorted(LIMITS)}`
  - `update(conn, changes: dict, source: str, confirm: str | None = None) -> dict` — `source` 는 `"web"` 또는 `"cli"`. 검증을 모두 통과해야 한 트랜잭션으로 저장, 바뀐 값마다 `log_setting_change`, 바뀐 것이 있으면 `notify.send` 한 번 (`"⚙️ 설정 변경 (web): trading.submit False → True, ..."`). 반환은 새 `snapshot`.
- 규칙: 최상위 키는 `trading|paused|config` 만. `trading.broker ∈ {sim, kis}`, `trading.submit` bool, `trading.allow_real` 은 `source != "cli"` 이면 ControlError. `source == "web"` 에서 submit false→true 는 `confirm == "SUBMIT"` 필요. `paused` 키는 `KR|US`, 값 bool. `config` 키는 `LIMITS` 에 있는 것만, `bool` 거부, `int` 필드는 정수만, `float` 필드는 int/float 허용하되 유한값만 받아 `float` 로 저장, 범위 안.

- [ ] **Step 1: Write the failing tests** (`tests/test_control.py`, `db.connect(tmp_path / "c.db")` 사용)

```python
def test_snapshot_defaults(conn):
    s = control.snapshot(conn)
    assert s["trading"] == {"broker": "sim", "submit": False, "allow_real": False}
    assert s["paused"] == {"KR": False, "US": False}
    assert s["config"]["stock_pct"] == 60.0 and "kr_taxable" not in s["config"]
    assert "stock_pct" in s["editable"] and "us_bond" not in s["editable"]

def test_update_logs_and_notifies_once(conn, no_real_alerts):
    control.update(conn, {"config": {"stock_pct": 70, "confirm_days": 4}, "paused": {"KR": True}}, "web")
    rows = db.setting_changes(conn)
    assert {r["key"] for r in rows} == {"config.stock_pct", "config.confirm_days", "paused.KR"}
    assert all(r["source"] == "web" for r in rows)
    assert len(no_real_alerts) == 1 and "config.stock_pct 60.0 → 70.0" in no_real_alerts[0]  # float fields stored as float
    assert jobs.load_config(conn).stock_pct == 70

def test_noop_update_writes_nothing(conn, no_real_alerts):
    control.update(conn, {"config": {"stock_pct": 60.0}}, "web")
    assert db.setting_changes(conn) == [] and no_real_alerts == []

@pytest.mark.parametrize("changes", [
    {"config": {"confirm_days": True}},          # bool is not an int here
    {"config": {"confirm_days": 2.5}},
    {"config": {"stock_pct": float("nan")}},
    {"config": {"stock_pct": 101}},
    {"config": {"us_bond": "TLT"}},              # read-only field
    {"trading": {"broker": "ib"}},
    {"paused": {"JP": True}},
    {"other": {}},
])
def test_invalid_changes_rejected(conn, changes):
    with pytest.raises(control.ControlError):
        control.update(conn, changes, "web")
    assert db.setting_changes(conn) == []

def test_submit_on_from_web_needs_confirm_and_is_atomic(conn):
    with pytest.raises(control.ControlError, match="SUBMIT"):
        control.update(conn, {"trading": {"submit": True}, "config": {"stock_pct": 70}}, "web")
    assert control.snapshot(conn)["config"]["stock_pct"] == 60.0     # nothing from that request saved
    control.update(conn, {"trading": {"submit": True}}, "web", confirm="SUBMIT")
    assert control.trading_settings(conn)["submit"] is True
    control.update(conn, {"trading": {"submit": False}}, "web")      # turning off needs no confirm

def test_allow_real_is_cli_only(conn):
    with pytest.raises(control.ControlError):
        control.update(conn, {"trading": {"allow_real": True}}, "web")
    control.update(conn, {"trading": {"allow_real": True}}, "cli")
    assert control.trading_settings(conn)["allow_real"] is True
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q tests/test_control.py` — Expected: FAIL (`ModuleNotFoundError: control`)
- [ ] **Step 3: Implement** `db.py` 테이블·함수, `control.py` (위 Interfaces). `jobs.py` 는 `trading_settings` 를 `control.trading_settings` 로 바꾸고, `config` 명령은 `control.update(conn, {"trading": {...}}, "cli")` 결과를 출력한다 (출력 형식 `trading settings: {...}` 유지).
- [ ] **Step 4: Run** `.venv/bin/python -m pytest -q` — Expected: 전부 PASS (기존 54 + 새 테스트)
- [ ] **Step 5: Commit** `git add control.py db.py jobs.py tests/test_control.py && git commit` — 제목 `Add settings service with change log and alerts`

### Task 2: 시장별 일시정지

**Files:**
- Modify: `jobs.py` (`tick`, `status`, `config` 인자 `--pause`/`--resume`)
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `control.paused(conn) -> dict[str, bool]`, `control.update(...)`
- Produces: `tick()` 이 일시정지된 시장마다 `"<CODE> paused"` 한 줄을 출력 목록에 넣고 그 시장 signal/execute 를 건너뜀 (오늘 세션이 있고 아직 마감 전일 때만 줄을 넣는다 — 기존 `if s is None or now >= s.close: continue` 다음 위치). CLI `jobs.py config --pause {KR,US,ALL}` / `--resume {KR,US,ALL}`. `status` 첫 줄 뒤에 `paused KR=False US=False`.

- [ ] **Step 1: Write the failing test**

```python
def test_paused_market_is_skipped_but_report_runs(env, no_real_alerts):
    conn, src, cfg = env
    set_day(src, "2026-10-27", {"AAPL": 5000, "NVDA": 4900})
    src.quote_time = pd.Timestamp("2026-10-27 12:45", tz=NY)       # fresh for the 12:50 tick
    at = pd.Timestamp("2026-10-27 12:50", tz=NY)                    # past US signal/execute; 01:50 KST, no report yet
    control.update(conn, {"paused": {"US": True}}, "cli")
    assert jobs.tick(conn, src, cfg, at, fx=lambda: 1400.0) == ["US paused"]
    assert db.run_status(conn, "signal", "US", "2026-10-27") is None
    control.update(conn, {"paused": {"US": False}}, "cli")
    jobs.tick(conn, src, cfg, at, fx=lambda: 1400.0)
    assert db.run_status(conn, "signal", "US", "2026-10-27") == "OK"
    control.update(conn, {"paused": {"KR": True, "US": True}}, "cli")
    out = jobs.tick(conn, src, cfg, pd.Timestamp("2026-10-28 08:05", tz="Asia/Seoul"), fx=lambda: 1400.0)
    assert "KR paused" in out                                       # KR session 10-28 not closed yet
    assert any(o.startswith("report ALL 2026-10-28") for o in out)  # pausing never stops the daily report
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q tests/test_jobs.py -k paused` — Expected: FAIL (`"US paused" not in out`)
- [ ] **Step 3: Implement** `tick` 루프 시작에서 `paused = control.paused(conn)` 을 한 번 읽고 해당 시장은 `continue` (세션이 있을 때만 `"<CODE> paused"` 추가). `config` 에 `--pause`/`--resume` (choices `KR, US, ALL`) 추가.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest -q` — Expected: 전부 PASS
- [ ] **Step 5: Commit** — 제목 `Pause a market from settings instead of the timer`

### Task 3: API 백엔드 (`api.py`)

**Files:**
- Create: `api.py`, `tests/test_api.py`
- Modify: `requirements.txt` (`fastapi>=0.162`, `uvicorn>=0.54`, `httpx>=0.28`), `.env.example` (`MCR_API_TOKEN=` 와 생성 방법 주석: `python -c "import secrets; print(secrets.token_urlsafe(32))"`), `AGENTS.md` 코드 구조 표에 `control.py`, `api.py`
- Test: `tests/test_api.py` (`fastapi.testclient.TestClient`)

**Interfaces:**
- Consumes: `control.snapshot/update/ControlError/LIMITS/paused/trading_settings`, `db.setting_changes`, `webreport.render(db_path)`, `webreport.db_version(db_path)`, `markets.current_session(mkt, now)`
- Produces:
  - `load_token(path: Path = ENV_FILE) -> str | None` — 빈 문자열은 None
  - `create_app(db_path: Path | str, token: str | None, web_dir: Path = Path(__file__).with_name("frontend") / "dist", db_timeout: float = 3.0) -> FastAPI` — `FastAPI(title="market-cap-rebalancer", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)`, `/app` 은 `web_dir` 이 있을 때만 `StaticFiles(directory=web_dir, html=True)` 로 마운트
  - 요청 모델 `SettingsPatch(BaseModel)`: `trading: dict | None`, `paused: dict | None`, `config: dict | None`, `confirm: str | None` (세부 검증은 control 이 한다)
  - DB 연결은 요청마다 `sqlite3.connect(db_path, timeout=db_timeout)` + `row_factory`, 처음 한 번 `db.connect(db_path)` 로 스키마 보장. `sqlite3.OperationalError("database is locked")` → 503 `{"detail": "DB busy, retry"}`

- [ ] **Step 1: Write the failing tests**

```python
TOKEN = "t" * 32
H = {"Authorization": f"Bearer {TOKEN}"}

def test_api_requires_token(client):
    assert client.get("/api/v1/settings").status_code == 401
    assert client.get("/api/v1/settings", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v1/settings", headers=H).status_code == 200

def test_api_closed_without_configured_token(tmp_path):
    c = TestClient(api.create_app(tmp_path / "a.db", token=None))
    assert c.get("/api/v1/status", headers=H).status_code == 503
    assert c.get("/").status_code == 200                       # old report still served

def test_patch_settings_roundtrip(client, no_real_alerts):
    r = client.patch("/api/v1/settings", headers=H, json={"config": {"stock_pct": 70}})
    assert r.status_code == 200 and r.json()["config"]["stock_pct"] == 70
    log = client.get("/api/v1/settings/log", headers=H).json()
    assert log[0]["key"] == "config.stock_pct" and log[0]["source"] == "web"

def test_patch_rejections(client):
    r = client.patch("/api/v1/settings", headers=H, json={"trading": {"submit": True}})
    assert r.status_code == 400 and "SUBMIT" in r.json()["detail"]
    assert client.patch("/api/v1/settings", headers=H, json={"trading": {"allow_real": True}}).status_code == 400

def test_patch_when_db_locked_returns_503(client, db_path):
    lock = sqlite3.connect(db_path); lock.execute("BEGIN IMMEDIATE")
    try:
        r = client.patch("/api/v1/settings", headers=H, json={"config": {"stock_pct": 70}})
        assert r.status_code == 503 and "retry" in r.json()["detail"]
    finally:
        lock.rollback(); lock.close()

def test_status_lists_markets_and_runs(client):
    s = client.get("/api/v1/status", headers=H).json()
    assert set(s["markets"]) == {"KR", "US"} and "paused" in s and isinstance(s["runs"], list)

def test_openapi_and_report(client):
    assert "/api/v1/settings" in client.get("/api/openapi.json").json()["paths"]
    assert client.get("/").text.startswith("<!doctype html>")
    assert client.get("/version").status_code == 200
```

  (픽스처 `db_path` = `tmp_path / "a.db"` 를 `db.connect` 로 만든 경로, `client = TestClient(api.create_app(db_path, TOKEN, db_timeout=0.2))`.)

- [ ] **Step 2: Run** `.venv/bin/pip install -r requirements.txt && .venv/bin/python -m pytest -q tests/test_api.py` — Expected: FAIL (`ModuleNotFoundError: api`)
- [ ] **Step 3: Implement** `api.py`. 인증은 `HTTPBearer(auto_error=False)` 의존성에서 토큰 미설정 → 503, 불일치·누락 → 401, 비교는 `hmac.compare_digest`. `/api/v1/status` 의 시장 시각은 `current_session(MARKETS[c], now)` 의 `signal/execute/close` 를 ISO(UTC)로, 세션이 없으면 null. `runs` 는 `SELECT * FROM runs ORDER BY started_at DESC LIMIT 10`.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest -q` — Expected: 전부 PASS
- [ ] **Step 5: Commit** — 제목 `Add v1 settings API with bearer token`

### Task 4: `jobs.py web --serve` 를 API 서버로 교체

**Files:**
- Modify: `jobs.py` (`web` 명령), `webreport.py` (`make_server`, `serve` 삭제), `tests/test_jobs.py` (`test_served_page_tracks_db_changes` → `tests/test_api.py` 로 옮겨 TestClient 로 `/` 와 `/version` 이 DB 변경을 따라가는지 확인), `deploy/DEPLOY.md` (5번 절: 토큰 설정, `http://127.0.0.1:8765/app/`), `README.md` 웹 리포트 절
- Test: `tests/test_api.py::test_served_page_tracks_db_changes`

**Interfaces:**
- Consumes: `api.create_app`, `api.load_token`
- Produces: `jobs.py web --serve [--host] [--port]` → `uvicorn.run(api.create_app(a.db, api.load_token()), host=a.host, port=a.port, log_level="warning")`. 포트 사용 중이면 지금과 같은 `SystemExit("cannot listen on ...; already running?")` 문구. `--serve` 없이 `web` 은 지금처럼 `site/index.html` 만 쓴다.

- [ ] **Step 1: Move the test** — 기존 단언(DB 변경 시 `/version` 값이 바뀌고 `/` 에 `data-version` 이 있음)을 TestClient 로 옮긴다. 기존 `webreport.make_server` 를 쓰는 부분은 지운다.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q tests/test_api.py -k served_page` — Expected: PASS 가 아니면 Task 3 의 `/`·`/version` 구현을 고친다 (이 단계는 이동한 테스트가 새 서버에서도 같은 계약을 지키는지 확인)
- [ ] **Step 3: Implement** `jobs.py` `web --serve` 교체, `webreport.make_server/serve` 삭제
- [ ] **Step 4: Verify manually on a temp DB**

```bash
.venv/bin/python jobs.py --db /tmp/mcr-web-test.db init --usd 1000 --krw 1000000
MCR_API_TOKEN=test-token .venv/bin/python jobs.py --db /tmp/mcr-web-test.db web --serve --port 8799 &
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8799/                                   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8799/api/v1/settings                    # 401
curl -s -H 'Authorization: Bearer test-token' http://127.0.0.1:8799/api/v1/settings | head -c 200   # JSON
kill %1
```

- [ ] **Step 5: Run** `.venv/bin/python -m pytest -q` — Expected: 전부 PASS
- [ ] **Step 6: Commit** — 제목 `Serve the report and API from one uvicorn server`

### Task 5: 설정 웹 UI (`frontend/`, React + Vite + TypeScript)

Task 5a(뼈대·도구)는 Task 1 과 동시에 시작할 수 있다. Task 5b(화면)는 Task 3 의 OpenAPI 가 나온 뒤 실제 스키마로 타입을 다시 생성해 맞춘다.

**Files:**
- Create: `frontend/` (`npm create vite@9.3.4 frontend -- --template react-ts` 로 시작), `frontend/src/api/client.ts`, `frontend/src/api/schema.d.ts`, `frontend/src/components/{StatusPanel,TradingPanel,StrategyPanel,ChangeLog,TokenGate}.tsx`, 각 `*.test.tsx`, `frontend/src/test/setup.ts`, `scripts/export_openapi.py`
- Modify: `.gitignore` (`frontend/node_modules/`, `frontend/dist/`), `README.md` ("웹 설정" 절: Node 설치, `npm ci && npm run build`, 토큰 입력, 반영 시점 = 다음 tick), `AGENTS.md` (코드 구조 표에 `frontend/`, 프런트 검증 명령 4개)
- Test: `frontend/src/**/*.test.tsx` (Vitest + Testing Library + jsdom), `tests/test_api.py::test_settings_ui_is_served`

**Interfaces:**
- Consumes: Task 3 API. 타입은 `python scripts/export_openapi.py` → `frontend/openapi.json` → `npx openapi-typescript openapi.json -o src/api/schema.d.ts` (npm 스크립트 `gen:api`). 앱도 이 `schema.d.ts` 를 그대로 쓴다.
- Produces:
  - `frontend/package.json` 스크립트: `dev`, `build`, `lint`, `typecheck`, `test`, `gen:api`. `vite.config.ts` 의 `base: "/app/"`, 개발 서버 프록시 `/api` → `http://127.0.0.1:8765`.
  - `client.ts`: `createClient(token: string)` → `{ getStatus(), getSettings(), patchSettings(body), getLog(limit?), getOrders(limit?) }`. 모든 요청에 `Authorization: Bearer`. 401 → `UnauthorizedError`, 그 밖의 오류 → 응답 `detail` 을 담은 `ApiError`.
  - 토큰은 `sessionStorage["mcr_token"]`. 401 이면 지우고 `TokenGate` 로 돌아감.
- 화면 (한 페이지, 위에서 아래):
  1. `StatusPanel`: 시장별 오늘 signal/execute 시각(KST 표시), 일시정지 여부, 최근 실행 10건. 30초마다 다시 읽음.
  2. `TradingPanel`: broker(sim/kis), 주문 전송 토글 — 켤 때 확인 대화상자에 `SUBMIT` 을 직접 입력해야 `confirm` 과 함께 PATCH, 시장별 일시정지 토글, `allow_real` 은 읽기 전용 + "CLI 전용" 안내.
  3. `StrategyPanel`: `limits` 로 만든 숫자 입력(min/max, int 는 step 1), 저장 전 바뀐 항목 목록 확인, 안내 "백테스트 결과(results/summary.md)는 기본값 기준입니다. 값을 바꾸면 그 결과와 비교할 수 없습니다."
  4. `ChangeLog`: `/api/v1/settings/log` 표.
  5. 링크: 투자 리포트(`/`), API 문서(`/api/docs`).
  - 변경 성공 뒤 "다음 tick(최대 5분 뒤)부터 적용" 표시. 400/503 의 `detail` 을 그대로 표시. 외부 CDN·폰트 금지. 라이트/다크는 `prefers-color-scheme`.

- [ ] **Step 1 (5a): Scaffold** — create-vite react-ts, 위 의존성으로 고정(특히 `typescript@~5.9.3`), Vitest·Testing Library·ESLint 설정, `base: "/app/"`, `.gitignore`. 확인: `npm run lint && npm run typecheck && npm test -- --run && npm run build` 모두 종료 코드 0.
- [ ] **Step 2 (5a): Write failing component tests** (`fetch` 를 `vi.fn` 으로 대체)

```tsx
it("asks for a token and keeps it only in sessionStorage", ...)        // TokenGate 제출 → sessionStorage.mcr_token 설정, localStorage 비어 있음
it("returns to the token gate on 401", ...)                            // getSettings 401 → TokenGate 다시 표시, sessionStorage 비움
it("requires typing SUBMIT before enabling order submission", ...)     // 토글 → 대화상자, "submit" 입력 시 PATCH 호출 없음, "SUBMIT" 입력 시 body {trading:{submit:true}, confirm:"SUBMIT"}
it("shows allow_real as read-only", ...)                               // allow_real 컨트롤 disabled, "CLI 전용" 문구
it("sends only changed strategy fields and shows the server detail on 400", ...)  // stock_pct 70 만 바꾸면 body {config:{stock_pct:70}}; 400 {"detail":"..."} → 그 문구 표시
it("renders KST times for each market", ...)                           // "2026-11-09T16:45:00+00:00" → "01:45" (다음날) 표시
```

- [ ] **Step 3 (5a): Run** `npm test -- --run` — Expected: FAIL (컴포넌트 없음)
- [ ] **Step 4 (5a): Implement** `client.ts` 와 컴포넌트. 타입은 처음에는 이 계획의 API 표를 보고 손으로 쓴 임시 `schema.d.ts` 로 시작한다.
- [ ] **Step 5 (5a): Run** 프런트 검증 4개 — Expected: 전부 통과. **Commit** — 제목 `Add React settings UI scaffold and components`
- [ ] **Step 6 (5b, Task 3 이후): Generate types from the real API** — `scripts/export_openapi.py` 추가, `npm run gen:api` 로 `schema.d.ts` 교체, 타입 오류 수정.
- [ ] **Step 7 (5b): Write the failing server test**

```python
def test_settings_ui_is_served(tmp_path):
    dist = tmp_path / "dist"; dist.mkdir(); (dist / "index.html").write_text("<title>설정</title>", encoding="utf-8")
    c = TestClient(api.create_app(tmp_path / "a.db", TOKEN, web_dir=dist))
    assert "<title>설정</title>" in c.get("/app/").text
    assert TestClient(api.create_app(tmp_path / "b.db", TOKEN, web_dir=tmp_path / "missing")).get("/app/").status_code == 404
```

- [ ] **Step 8 (5b): Run** `.venv/bin/python -m pytest -q` 과 프런트 검증 4개 — Expected: 전부 통과 (Task 3 의 마운트 조건이 이미 맞으면 Step 7 은 바로 PASS 일 수 있다)
- [ ] **Step 9 (5b): Verify in a browser** — `npm run build` 후 Task 4 Step 4 처럼 임시 DB·포트 8799 로 띄우고 `http://127.0.0.1:8799/app/` 확인: 잘못된 토큰 → 토큰 입력 복귀 / `stock_pct` 70 저장 → 이력 한 줄 / 주문 전송 켜기에서 `SUBMIT` 아닌 값 → 변경 없음 / KR 일시정지 → 상태에 표시. playwright-remote 로 스크린샷을 남긴다.
- [ ] **Step 10 (5b): Commit** — 제목 `Generate UI types from the API and serve the build`

### Task 6: 서버 반영 (사용자 확인 필요 단계 포함)

**Files:** 없음 (운영 작업). 기록은 `deploy/DEPLOY.md` 에 Task 4 에서 이미 반영.

- [ ] **Step 1:** 통합 브랜치를 사용자 승인 뒤 `main` 에 병합 (실행 중 checkout 이므로 `systemctl is-active mcr-tick.service` 가 `inactive` 일 때). 이어서 `.venv/bin/pip install -r requirements.txt && .venv/bin/python -m pytest -q && (cd frontend && npm ci && npm run build)` — Expected: 전부 PASS, `frontend/dist/index.html` 생성
- [ ] **Step 2 [사용자]:** `.env` 에 `MCR_API_TOKEN` 추가 (작업자는 값을 보지 않는다). 생성: `.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))"`
- [ ] **Step 3 [사용자]:** `sudo systemctl restart mcr-web.service` (유닛 파일은 바뀌지 않으므로 `install.sh` 재실행 불필요)
- [ ] **Step 4:** `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/api/v1/status` → `401`, `systemctl is-active mcr-web` → `active`, `journalctl -u mcr-web -n 20` 에 오류 없음
- [ ] **Step 5:** 다음 tick 뒤 `jobs.py status` 와 웹 상태 섹션이 같은 값을 보이는지 확인

---

## 실행 방식 (사용자 결정 2026-11-09: herdr 로 다른 탭에 코딩 에이전트)

- **격리:** 서비스가 `/data/workspace/github/market-cap-rebalancer` checkout 을 그대로 실행한다. 에이전트는 이 checkout 을 고치지 않고, `.worktrees/<이름>` git worktree(로컬 제외: `.git/info/exclude`)에서 브랜치로 일한다. worktree 마다 자기 `.venv` 를 만든다 — 운영 `.venv` 에 패키지를 설치하지 않는다.
- **브랜치:** `feat/web-backend` (Task 1→2→3→4 순서), `feat/web-frontend` (Task 5a, Task 3 뒤 5b). 둘 다 `main` 에서 시작. 마지막에 `feat/web-settings` 로 합쳐 전체 검증 → 사용자 승인 → `main` (Task 6).
- **에이전트 (herdr 탭 `mcr-dev`):** 백엔드 = `codex` · `gpt-6.1-terra`(high), 프런트 = `claude-per` · `sonnet`, 독립 확인 = `agy` · `gemini-3.8-flash-high`(결론을 알려 주지 않고 diff·테스트 근거만 대조). 지시는 Task 하나씩, 완료 조건·확인 명령·기대 출력과 함께 준다.
- **오케스트레이터(이 세션) 검증:** Task 마다 diff 를 읽고, 테스트·lint·typecheck 를 직접 다시 실행하고, 통과해야 다음 Task 를 지시한다. 같은 Task 가 두 번 검증을 통과하지 못하면 다른 2순위 에이전트로 바꾼다.

## 이후 단계 (앱 확장, 이번 계획 밖)

1. 원격 접속: Tailscale 사설망 또는 리버스 프록시(HTTPS + 인증). 이때 기존 리포트 `/` 도 인증 뒤로 옮긴다.
2. 기기별 토큰(발급·폐기), 토큰을 `.env` 하나에서 별도 저장소로.
3. PWA 매니페스트(선택지 C) 또는 네이티브 앱 — `/api/openapi.json` 으로 클라이언트 생성.
4. 앱 푸시 알림 (지금은 텔레그램).
