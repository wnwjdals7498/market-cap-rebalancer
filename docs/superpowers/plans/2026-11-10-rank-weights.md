# 시총 1~5위 비율 전략 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 리더(1위 단독/1·2위 반반) 전략을 "시장별 주식 5종목 시총 순위 × 설정 비율 + 단기/장기채 + 매매 1회당 이동 상한 + 매매 주기 + 미국/한국 비율(수동 환전 안내)"로 바꾸고, 전부 웹·CLI 설정으로 조정한다.

**Architecture:** 규칙은 `strategy.py` 의 순수 함수(백테스트·실운용 공용). `jobs.py` 가 SQLite 상태(`rank_state`, `settings` 의 `fx:plan`·`retired`)를 읽고 써서 매일 signal(조사)·주기별 execute(매매)·08:00 보고(환전 안내)를 돌린다. 설정은 `control.py` 가 타입별로 검증하고 `api.py`/`frontend` 가 그대로 노출한다.

**Tech Stack:** Python 3.12, pandas, exchange_calendars, SQLite, FastAPI/pydantic v2, React 19 + Vite + TS 5.9, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-10-rank-weights-design.md`

## Global Constraints

- 브랜치 `feat/rank-weights`(worktree `.worktrees/rank`), 백테스트 작업은 `feat/rank-backtest`(worktree `.worktrees/rank-bt`). main 에는 Task 7 이 끝난 뒤 한 번만 병합한다 — AGENTS.md "전략 규칙 + 백테스트 + README 규칙 표는 같은 커밋" 규칙을 main 기준으로 지킨다.
- 단계 하나 = 커밋 하나. 커밋 전 `.venv/bin/python -m pytest -q` 통과. 프런트 변경 시 `cd frontend && npm run lint && npm run typecheck && npm test -- --run && npm run build` 모두 0. `api.py` 변경 시 `.venv/bin/python scripts/export_openapi.py && (cd frontend && npm run gen:api)`.
- 데이터 검증 상수(`STALE` 40분, `SHARES_JUMP` 0.20, `MCAP_JUMP` 0.40) 완화 금지. `runs` 1세션 1회 보장 우회 금지. 시각은 `exchange_calendars` 로만.
- 테스트는 네트워크 금지. `mcr.db`, `.env` 를 열거나 쓰지 않는다. 수동 실험은 `--db <scratch>`.
- 배포 값 = 코드 기본값: `rank_weights=(50,30,20,0,0)`, `stock_pct=60`, `short_bond_pct=100`, `us_pct=60`, `confirm_days=0`, `trade_every="week"`, `step_pct=10`, `fx_band_pp=5`, `fx_enabled=True`, `fx_wait_days=3`.
- 후보: `us_universe=("AAPL","NVDA","MSFT","GOOGL","AMZN")`, `kr_universe=("005930.KS","000660.KS","402340.KS","373220.KS","005380.KS")`, `us_short_bond="SHY"`, `us_long_bond="TLT"`, `kr_short_bond="114260.KS"`, `kr_long_bond="385560.KS"`.
- 이동 상한: 매매 1회에 매도 합계와 매수 합계가 각각 `step_pct`% × 포켓 자산 이하. 예외: 포켓에 현금 외 관리 종목 보유가 0 일 때의 첫 매수.
- 관리 범위는 종목 단위: 후보 ∪ 채권 2종 ∪ `retired` ∪ CASH. 그 밖의 종목은 읽기만 하고 매매하지 않는다.
- 설정 필드 설명은 한국어, docstring 영어(주변 코드 따름). 커밋 메시지 영어 명령형.

## Review Focus

1. **역사·실시간 데이터가 없는 후보** (402340 은 2021-11 상장, 백테스트 시작 2021-10-26; 실시간 시세 누락) → 그날 순위에서 빼고 남은 순위 비율을 다시 정규화. 실시간은 기존처럼 의심 데이터면 전날 확정 순위 유지(HOLD). → Task 1 `test_stock_weights_renormalize_when_fewer_names`, Task 6 `test_backtest_handles_ticker_listed_midway`.
2. **주의 첫 거래일이 휴장·실패** → 같은 주 다음 거래일에 매매. → Task 3 `test_weekly_trade_retries_after_failed_first_session`.
3. **주중에 웹에서 후보를 바꿈** → 빠진 종목은 `retired` 로 가서 상한 안에서 매도, 목록 밖 개인 보유는 그대로. → Task 2 `test_universe_change_retires_removed_tickers`, Task 3 `test_retired_ticker_sold_within_cap_and_dropped_at_zero`.
4. **총자산 0 또는 한쪽 포켓 미동기화(KR 현금 0, DRY_RUN)** → 환전 계획을 만들지 않거나, 만들더라도 금액이 0 이하이면 만들지 않는다. → Task 4 `test_no_fx_plan_when_total_zero`.
5. **운영 DB 의 옛 설정**(`confirm_days 3`, `us_pct 50`, `pair_confirm_days`… 저장) → `migrate` 가 한 번만 바꾸고 다시 돌아도 변화 없음. → Task 2 `test_migrate_old_config_once`.

---

### Task 1: 전략 핵심 (strategy.py)

**Files:** Modify `strategy.py`; Create `tests/test_strategy.py`.

**Interfaces:**
- Produces (모두 `strategy.py`, 옛 `LeaderState`/`update_state`/`plan_targets(…, bond, …)`/`needs_rebalance`/`rebalance_pocket` 은 Task 7 까지 그대로 둔다 — 새 함수는 이름 충돌을 피해 아래 이름으로):
  - `Config` 새 필드(Global Constraints 의 기본값): `rank_weights: tuple[float, ...]`, `short_bond_pct: float`, `trade_every: str`, `fx_band_pp: float`, `fx_enabled: bool`, `fx_wait_days: int`, `us_universe: tuple[str, ...]`, `kr_universe: tuple[str, ...]`, `us_short_bond`, `us_long_bond`, `kr_short_bond`, `kr_long_bond: str`. `us_pct` 기본 60, `confirm_days` 기본 0. 메서드 `universe(market: str) -> tuple[str, ...]`, `bonds(market: str) -> tuple[str, str]` (short, long).
  - `@dataclass class RankState: confirmed: list[str]; pending: list[str]; streak: int` (기본 빈 리스트/0).
  - `update_ranking(state: RankState, ranked: list[str], cfg: Config) -> list[str]`: 확정 순위 반환(상태 변경). 규칙: `confirm_days == 0` 이거나 `confirmed` 가 비었거나 `set(ranked) != set(confirmed)`(후보 변경) → 즉시 확정. `ranked == confirmed` → pending 초기화. `ranked == pending` → streak+1, 아니면 pending=ranked, streak=1. `streak >= confirm_days` → 확정.
  - `stock_weights(ranking: list[str], cfg: Config) -> dict[str, float]`: `rank_weights[i]` 를 `ranking[i]` 에, 이름 수만큼만 쓰고 합 1 로 정규화, 0 비중 제외. 쓸 비중 합이 0 이면 `{}`.
  - `pocket_weights(stock_w: dict[str, float], market: str, cfg: Config) -> dict[str, float]`: 주식 = `stock_w × stock_pct/100`(stock_w 가 비면 주식 0), 나머지 채권 몫을 `short_bond_pct` 로 단기/장기 분할, 0 제외, 합 1.
  - `plan_moves(units: dict[str, float], prices: dict[str, float], weights: dict[str, float], cfg: Config, investable: float) -> dict[str, float]`: 목표 금액. `cur`=현금 외 보유 금액, `want`=weights×investable(weights 밖 보유는 0), 매도 합 `out`, 매수 합 `inn`, `cap = step_pct/100 × pocket_value`, `α = 1 if not cur else min(1, cap / max(out, inn))`(분모 0 이면 1), `target = cur + α(want−cur)`, 1e-9 이하 제거.
  - `needs_moves(units, prices, weights, cfg) -> bool`, `rebalance_moves(units, prices, weights, market, cfg) -> tuple[dict[str, float], float, float]`: 옛 `needs_rebalance`/`rebalance_pocket` 과 같은 역할을 `plan_moves` 기준으로(밴드 규칙: `band_pp == 0` 이면 차이 1e-6 초과 시, 아니면 구성 변화 또는 어떤 종목 차이 > band_pp).
  - `trade_due(day: datetime.date, last_trade: datetime.date | None, every: str) -> bool`: 기간 키 day=날짜, week=`isocalendar()[:2]`, month=(연, 월). `last_trade` 가 None 이거나 기간이 다르면 True.
  - `fx_gap(us_krw: float, kr_krw: float, cfg: Config) -> float`: 미국으로 옮길 원화(+)/미국에서 뺄 원화(−). `not cfg.fx_enabled`, 총 0, `|미국비중−us_pct| ≤ fx_band_pp` 이면 0.0.
  - `fx_amount(gap_krw: float, source_krw: float, cfg: Config) -> float`: `min(|gap|, step_pct/100 × source_krw)`.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_strategy.py`):
  - `test_update_ranking_immediate_when_confirm_zero`: confirm_days=0, ranked `["B","A","C","D","E"]` → 반환 그대로.
  - `test_update_ranking_waits_confirm_days`: confirm_days=2, 확정 `[A..E]` 상태에서 `[B,A,…]` 1일 → 옛 순위, 2일 → 새 순위; 중간에 다른 순위가 끼면 streak 1 로 리셋.
  - `test_update_ranking_resets_on_universe_change`: 확정에 없는 종목이 들어오면 confirm_days=3 이어도 즉시 확정.
  - `test_stock_weights_by_rank`: (50,30,20,0,0), `[A,B,C,D,E]` → `{"A":.5,"B":.3,"C":.2}`.
  - `test_stock_weights_renormalize_when_fewer_names`: (50,30,20,0,0), `[A,B]` → `{"A":.625,"B":.375}`; (0,0,0,50,50) + `[A,B]` → `{}`.
  - `test_pocket_weights_split_bonds`: stock_pct 60, short 100 → 주식 합 .6, SHY .4, TLT 없음; short 25 → SHY .1, TLT .3.
  - `test_plan_moves_caps_sells_and_buys`: 포켓 1000(전부 X), weights `{Y:1}`, step 10 → X 900, Y 100. 현금 500 + X 500, weights `{X:1}`, step 10 → 매수 100 만큼만(X 600).
  - `test_plan_moves_first_investment_uncapped`: 현금만 1000 → 목표 그대로.
  - `test_trade_due_periods`: week — 같은 ISO 주 False, 다음 주 True; month — 같은 달 False; day — 다른 날 True; last None → True.
  - `test_fx_gap_band_and_disabled`: `fx_gap(100, 0, us_pct=60)` → `-40.0`; `fx_gap(64, 36, us_pct=60, fx_band_pp=5)` → `0.0`; `fx_enabled=False` → `0.0`; `fx_gap(0, 0, …)` → `0.0`.
  - `test_fx_amount_capped_by_step`: gap −40, source 100, step 10 → 10.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/test_strategy.py -q` → 실패(ImportError).
- [ ] **Step 3:** 위 Interfaces 대로 구현. 설정 필드 주석은 한국어 한 줄(기존 Config 형식).
- [ ] **Step 4:** `.venv/bin/python -m pytest -q` 전부 통과.
- [ ] **Step 5:** 커밋 `Add rank-weight strategy core alongside the leader rules`.

### Task 2: 설정 계층 (control, 마이그레이션, load_config, API 설정 타입)

**Files:** Modify `control.py`, `jobs.py`(load_config, main 에서 migrate 호출), `api.py`(Snapshot/Limit 타입, create_app 에서 migrate), `frontend/src/components/StrategyPanel.tsx`(타입 호환만), `frontend/openapi.json`, `frontend/src/api/schema.d.ts`; Tests `tests/test_control.py`, `tests/test_api.py`, `tests/test_jobs.py`.

**Interfaces:**
- Consumes: Task 1 `Config` 필드.
- Produces:
  - `control.LIMITS[key]` = `{"type": "float"|"int"|"bool"|"choice"|"weights"|"tickers"|"ticker", "label": str(한국어), "min"?, "max"?, "options"?: list[str], "market"?: "US"|"KR"}`. 새 키: `rank_weights`(weights, 5칸 각 0~100, 합 100±1e-6), `short_bond_pct`(float 0~100), `trade_every`(choice day/week/month), `fx_band_pp`(float 0~50), `fx_enabled`(bool), `fx_wait_days`(int 1~30), `us_universe`/`kr_universe`(tickers, 5개, 중복 없음, US `^[A-Z][A-Z.\-]{0,5}$`, KR `^\d{6}\.KS$`), 채권 4키(ticker, 같은 정규식). 삭제: `pair_confirm_days`, `solo_after_days`. 교차 검증(변경 후 전체 설정 기준): 채권 2종 서로 다름, 주식 후보와 겹치지 않음.
  - 허용 source: `web`, `cli`, `system`, `migration`. SUBMIT 확인 규칙은 web 에만.
  - settings 키 `retired`: `{"US": {ticker: "stock"|"bond"}, "KR": {...}}`. `control.update` 가 후보·채권 변경 시 빠진 종목을 역할과 함께 추가, 다시 후보가 되면 제거.
  - `control.migrate(conn: sqlite3.Connection) -> bool`: settings `config:version` 이 2 미만일 때, 저장된 config 에 옛 키가 하나도 없으면(새 DB, `init` 직후) `config:version=2` 만 기록하고 False. 옛 키가 있으면 저장된 config 에서 `pair_confirm_days`, `solo_after_days`, `us_bond`, `kr_bond`, `fx_mode` 삭제, `confirm_days=0`, `us_pct=60` 기록, `retired` 에 (옛 US 8종목 ∪ 옛 KR 5종목 ∪ 저장된/기본 옛 채권 IEF·148070.KS) − 새 목록을 역할과 함께 추가, `settings_log`(source `migration`), `config:version=2`, 알림 1회. 바꾸면 True, 이미 2 면 아무것도 안 하고 False.
  - `jobs.load_config(conn) -> Config`: 리스트→튜플, 모르는 키 무시, `kr_taxable = set(kr_universe) ∪ {retired KR 중 role "stock"}`.
  - `api.Limit`: `type` Literal 확장, `label: str`, `min/max: float | None`, `options: list[str] | None`, `market: str | None`. `Snapshot.config: dict[str, float | int | bool | str | list[float] | list[str]]`.
- [ ] **Step 1: 테스트**:
  - `test_control.py::test_typed_settings_validation`: `rank_weights` 합 99 → ControlError, `[50,30,20,0,0]` OK(저장값 리스트), 6칸 → 에러; `trade_every "year"` → 에러; `fx_enabled 1` → 에러(StrictBool 처럼 bool 만); `kr_universe` 에 `"5930.KS"` → 에러; 중복 → 에러; `us_short_bond` 를 `us_universe` 종목으로 → 에러.
  - `test_universe_change_retires_removed_tickers`: `us_universe` 에서 AMZN→META 로 바꾸면 `retired["US"]=={"AMZN":"stock"}`; 다시 AMZN 넣으면 제거.
  - `test_migrate_fresh_db_only_sets_version`: 빈 DB·`init` 직후 → False, retired 없음, settings_log 0행.
  - `test_migrate_old_config_once`: mcr.db 와 같은 옛 config(`confirm_days 3, us_pct 50, pair_confirm_days 3, solo_after_days 0, us_bond IEF, kr_bond 148070.KS, fx_mode separate, stock_pct 60, step_pct 10`) → migrate True, `load_config` 가 confirm_days 0·us_pct 60, retired US 에 `IEF:bond, META, AVGO, TSLA:stock`, KR 에 `207940.KS:stock, 148070.KS:bond`, settings_log source migration; 두 번째 migrate False, 로그 수 그대로.
  - `test_api.py::test_settings_expose_typed_limits`: GET settings → `limits.rank_weights.type == "weights"`, `config.rank_weights == [50,30,20,0,0]`, `limits.trade_every.options == ["day","week","month"]`, 모든 limit 에 label.
  - 기존 테스트 중 `pair_confirm_days`·`us_bond` 를 쓰는 것(test_control L15, L50)은 새 키로 바꾼다.
- [ ] **Step 2:** 실패 확인. **Step 3:** 구현, `jobs.main` 과 `api.create_app` 에서 연결 직후 `control.migrate` 호출(웹·CLI 어느 쪽이 먼저 떠도 한 번). OpenAPI 재생성, StrategyPanel 은 숫자 아닌 값은 읽기 전용 표시로 컴파일만 맞춘다(편집 UI 는 Task 5).
- [ ] **Step 4:** pytest + 프런트 4종 통과. **Step 5:** 커밋 `Add typed strategy settings and one-time migration`.

### Task 3: 실운용 순위 전략 (jobs.py, db.py, report.py, webreport.py)

**Files:** Modify `jobs.py`, `db.py`, `report.py`, `webreport.py`; Tests `tests/test_jobs.py`, `tests/test_db.py`.

**Interfaces:**
- Consumes: Task 1 함수, Task 2 `load_config`, `retired`.
- Produces:
  - 테이블 `rank_state(session_date TEXT, market TEXT, confirmed TEXT, pending TEXT, streak INTEGER, PRIMARY KEY(session_date, market))` (JSON 리스트). `db.load_rank_state(conn, market: str, before: str) -> RankState`, `db.save_rank_state(conn, session_date: str, market: str, state: RankState) -> None`. 옛 `leader_state` 는 읽지 않는다(테이블은 남김).
  - `db.last_trade_date(conn, market: str) -> str | None`: `runs` 에서 job `execute`, status `OK` 의 최신 session_date.
  - `jobs.managed(units, mkt, cfg, retired: dict[str, str]) -> dict[str, float]`.
  - signal: 후보는 `cfg.universe(code)`, 시세 대상 = 후보 + 채권 2종 + 보유 retired. 순위/의심 판정은 기존 그대로, 의심이면 전날 확정 순위 유지. 매매일(`trade_due(session, last_trade_date, cfg.trade_every)`)이면 `pocket_weights(stock_weights(confirmed))`(retired 는 0) 로 `plan_moves` → `save_targets`; 아니면 순위·상태만 저장하고 detail `"no trade (week)"` 형식, 상태 OK. 보유 0 인 retired 는 `retired` 에서 지운다(control.update source `system`).
  - execute: 그 세션 targets 가 없으면 `SKIPPED`, detail `매매 주기 아님`.
  - 보고·웹 리포트 머리글: `순위 A > B > C | 다음 매매 <주기> | 대기 n/N일` 형식(옛 SOLO/SPLIT 문구 제거). `report.NAMES` 에 SHY, TLT, 114260.KS(국고채3년), 385560.KS(국고채30년), 402340.KS(SK스퀘어) 추가. `webreport` 의 색 슬롯은 `load_config(conn).universe(market)`.
- [ ] **Step 1: 테스트** (기존 `test_jobs.py` 의 SOLO/SPLIT 기대값은 새 규칙으로 고친다; `set_day` 는 `cfg.universe("US")` 와 SHY/TLT 가격 사용):
  - `test_rank_weights_targets_on_trade_day`: 5종목 시총 순 E>D>C>B>A, 빈 포켓 현금 100k → targets E .3, D .18, C .12, SHY .4.
  - `test_non_trade_day_updates_ranking_only`: 같은 주 둘째 날 signal OK·targets 없음·rank_state 저장, execute SKIPPED `매매 주기 아님`.
  - `test_weekly_trade_retries_after_failed_first_session`: 월요일 signal 이 시세 오래됨으로 실패 → 화요일 매매.
  - `test_retired_ticker_sold_within_cap_and_dropped_at_zero`: retired US `IEF:bond` 를 포켓 43% 보유, step 10 → 첫 매매에 IEF 매도 ≤ 포켓 10%; 수량 0 이 된 뒤 signal 에서 retired 에서 빠짐.
  - `test_unmanaged_position_untouched`: 목록 밖 종목 보유는 targets·orders 에 없음.
  - `test_kr_taxable_includes_retired_stock`.
- [ ] **Step 2–4:** 실패 확인 → 구현 → pytest 통과.
- [ ] **Step 5:** 커밋 `Run the rank-weight strategy live with a trade cadence`.

### Task 4: 환전 계획 (jobs.py)

**Files:** Modify `jobs.py`, `report.py`(보고 줄); Tests `tests/test_jobs.py`.

**Interfaces:**
- Consumes: Task 1 `fx_gap`, `fx_amount`; Task 3 signal/report.
- Produces:
  - settings 키 `fx:plan`: `{"from": "US"|"KR", "amount": float(출발 포켓 통화), "created": "YYYY-MM-DD", "ready": "YYYY-MM-DD" | null}`.
  - `jobs.fx_review(conn, today: str, usdkrw: float, cfg: Config, send: bool) -> list[str]`: `run_report` 가 호출, 보고 본문 줄 반환. 순서: (1) 계획 있음 + 출발 포켓 현금 < amount×0.5 이고 ready 였음 → 환전 완료로 보고 계획 삭제, 알림. (2) 계획 ready + `today − ready > fx_wait_days` → 계획 삭제, `control.update(conn, {"config": {"fx_enabled": False}}, "system")`, 알림 "환전 대기 n일 초과: 현금 재투자, 환전 계획 꺼짐". (3) 계획 있음 + 출발 포켓 현금 ≥ amount → ready 기록(처음일 때 알림 "미국 포켓 현금 $X 를 원화로 환전하세요" 형식). (4) 계획 없음 → `fx_gap` ≠ 0 이면 `fx_amount` 로 계획 생성(원화→출발 통화 환산), 알림. (5) 계획 있음 + `fx_gap == 0` → 삭제, 알림 "비율 회복". 계획이 있는 동안 반환 줄에 매일 한 줄.
  - 포켓 값은 최신 `positions` × 그 가격(보고가 이미 쓰는 값), 미국은 `usdkrw` 로 환산. 총 0 이거나 amount ≤ 0 이면 계획을 만들지 않는다.
  - signal 매매일: 계획의 `from == mkt.code` 이고 ready 가 아니면 `investable = value − amount` 로 현금 확보. 계획이 없거나 다른 시장이면 그대로.
- [ ] **Step 1: 테스트**:
  - `test_fx_plan_created_then_cash_reserved`: US 100% (원화 환산), KR 0 → 보고 후 `fx:plan.from == "US"`, amount = US 포켓 10%(달러); 다음 US 매매일 targets 합 = value − amount.
  - `test_fx_plan_ready_alert_once_then_exchanged`: 현금 확보 후 보고 → ready·알림 1회, 다음 날 보고 알림 없음·줄 있음; 현금이 줄면 계획 삭제·알림.
  - `test_fx_wait_timeout_disables_fx`: ready 후 4일째 보고 → 계획 삭제, `fx_enabled False`(settings_log source system), 알림; 다음 매매일 현금 재투자.
  - `test_no_fx_plan_when_total_zero`, `test_no_fx_plan_when_disabled_or_in_band`.
- [ ] **Step 2–4.** **Step 5:** 커밋 `Plan manual FX moves toward the US/KR ratio`.

### Task 5: 상태 API 와 웹 설정 화면

**Files:** Modify `api.py`, `frontend/src/components/StrategyPanel.tsx`, `frontend/src/components/StatusPanel.tsx`, `frontend/src/api/types.ts`, `frontend/openapi.json`, `frontend/src/api/schema.d.ts`, `frontend/src/test/helpers.ts`, `frontend/src/App.test.tsx`; Tests `tests/test_api.py`.

**Interfaces:**
- Consumes: Task 2 LIMITS 타입, Task 3 `db.load_rank_state`, `db.last_trade_date`, Task 4 `fx:plan`.
- Produces:
  - `api.MarketStrategy`: `ranking: list[str]`(최신 rankings), `confirmed: list[str]`, `targets: dict[str, float]`(최신 targets 비중), `last_trade: str | None`. `api.FxPlan`: `from_market: str`, `amount: float`, `currency: str`, `created: str`, `ready: str | None`. `Status` 에 `strategy: dict[str, MarketStrategy]`, `fx: FxPlan | None` 추가.
  - StrategyPanel: `limits[key].type` 별 입력 — float/int 숫자, bool 체크박스, choice 셀렉트(`day`=일간, `week`=주간, `month`=월간), weights 숫자 5칸 + 합계 표시(100 아니면 저장 버튼 비활성), tickers 텍스트 5칸, ticker 텍스트 1칸. 라벨은 `limits[key].label`. 저장은 바뀐 키만 PATCH(기존 방식). 400 메시지 표시 유지.
  - StatusPanel: 시장별 순위·확정 순위·목표 비중·마지막 매매일, 환전 계획(없으면 "없음").
- [ ] **Step 1: 테스트**: `test_api.py::test_status_includes_strategy_and_fx`(빈 DB → strategy 두 시장 빈 리스트, fx null; fx:plan 저장 후 값 노출). Vitest: `weights editor blocks save until sum is 100`, `trade_every select sends week`, `status shows fx plan`.
- [ ] **Step 2–4:** 실패 → 구현 → pytest·프런트 4종·OpenAPI 재생성 결과 커밋 포함.
- [ ] **Step 5:** 커밋 `Show strategy state and edit typed settings in the web UI`.

### Task 6: 백테스트 (병렬, `feat/rank-backtest`, Task 1 커밋에서 분기)

**Files:** Modify `backtest.py`, `data.py`; Test `tests/test_backtest.py`(새 파일, 네트워크 없이 합성 데이터).

**Interfaces:**
- Consumes: Task 1 함수 전부.
- Produces:
  - `data.load_market_data(start, end=None, extra=None, refresh=False, mcap_tickers: list[str] | None = None)`: 시총은 `mcap_tickers` 만 계산(기본 = Config 기본 두 후보 합). 상장 전 구간은 NaN.
  - `backtest.run(md, cfg, start, end, capital_krw=100_000_000) -> dict`: 후보·채권은 `cfg` 에서. 날마다 순위 갱신(`update_ranking`, NaN 종목 제외), 매매일(`trade_due`, 시장별 마지막 매매일)에만 `rebalance_moves`. 환전: 두 시장이 열린 날 `fx_gap`/`fx_amount` 로 계획 → 출발 포켓 매매일에 investable 차감으로 현금 확보 → 다음 날 `fx_spread` 를 떼고 받는 포켓 현금으로 이동 → 받는 포켓 다음 매매일에 투자. `fx_wait_days` 는 백테스트에서 쓰지 않는다(사용자가 바로 환전한다고 가정, summary 에 명시).
  - 시나리오: `전략 (설정값)`, `1위 단독 (100/0/0/0/0)`, `순위 대기 3일`, `매매 일간`, `매매 월간`, `이동 상한 100% (즉시)`, `같은 60/40, 지수 (SPY / KODEX200)`, `전략, 수수료·세금·환전비용 0`, 단순보유 SPY·KODEX200·환율.
- [ ] **Step 1: 테스트**: `test_backtest_weekly_trades_once_per_week`(합성 10거래일 → 매매 2회), `test_backtest_handles_ticker_listed_midway`(한 종목 앞 5일 NaN → 오류 없이 나머지로 순위), `test_backtest_fx_moves_cash_toward_us_pct`.
- [ ] **Step 2–4.** 실제 데이터 실행은 Task 7 에서.
- [ ] **Step 5:** 커밋 `Backtest the rank-weight strategy with cadence and FX`.

### Task 7: 옛 규칙 제거, 백테스트 결과, 문서 (Task 3~6 병합 후)

**Files:** Modify `strategy.py`(LeaderState, update_state, _update_pair, 옛 plan_targets/needs_rebalance/rebalance_pocket 제거 — 새 함수는 이름 유지), `jobs.py`(current_weights, bond_of 제거), `markets.py`(Market.universe 제거), `data.py`(US_UNIVERSE/KR_UNIVERSE 제거 또는 Config 기본값 참조), `results/summary.md`, `README.md`(규칙 표 L7-17, 웹 설정 절), `AGENTS.md`(코드 구조 표 문구), `deploy/DEPLOY.md`(업데이트 절에 "첫 실행 시 설정 마이그레이션" 한 줄).
- [ ] **Step 1:** `git merge feat/rank-backtest`(충돌 해결).
- [ ] **Step 2:** 제거 후 `grep -rn "LeaderState\|SOLO\|SPLIT\|pair_confirm\|solo_after\|us_bond\|kr_bond\|fx_mode" --include=*.py --include=*.ts --include=*.tsx .` → 마이그레이션 상수·테스트 외 0건.
- [ ] **Step 3:** `PYTHONWARNINGS=ignore .venv/bin/python backtest.py` (캐시 `cache/`, 네트워크 사용) → `results/summary.md` 갱신, 숫자 표를 README 규칙 설명과 맞춤.
- [ ] **Step 4:** pytest·프런트 4종·`uvx mypy@latest --ignore-missing-imports --follow-imports=silent strategy.py jobs.py control.py api.py backtest.py` 오류 수가 main 기준(jobs.py 7건) 이하.
- [ ] **Step 5:** 커밋 `Remove the leader rules; document and backtest rank weights`.

### Task 8: 배포 (사용자 확인 단계 포함)

- [ ] tick 비활성 시점(`systemctl is-active mcr-tick.service` = inactive)에 main 으로 fast-forward 병합(사전에 worktree 에서 main 병합·충돌 해결).
- [ ] `.venv/bin/python -m pytest -q`, `cd frontend && npm ci && npm run build`.
- [ ] `jobs.py status` 1회로 migrate 실행 → `settings_log` 에 migration 행, `config:version` 2 확인(읽기 전용 sqlite).
- [ ] [사용자] `sudo systemctl restart mcr-web.service`.
- [ ] 다음 매매일 signal/execute 결과(DRY_RUN PLANNED 주문)와 08:00 보고의 환전 줄 확인.
