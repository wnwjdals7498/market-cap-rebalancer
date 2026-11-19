# 금 ETF·지수 ETF 편입 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 포켓을 "위험자산(stock_pct) / 안전자산(나머지)" 두 몫으로 보고, 위험자산은 시장별로 개별주(상위 5, `rank_weights`)·지수 ETF 2개에, 안전자산은 단기채·장기채·금 ETF 에 비율(각각 합 100)로 나눈다.

**Architecture:** `strategy.pocket_weights` 하나가 백테스트와 실운용의 비율 계산을 모두 맡는다(AGENTS.md). 새 수치·종목은 전부 `Config` 필드 + `control.LIMITS` 로 추가하고, 의미가 바뀐 `short_bond_pct` 는 `control.migrate` 로 옮긴다. 채권·금·지수 ETF 는 모두 "ETF"(거래세 면제, ETF 호가단위, 시총 검증 없음, 가격 신선도만 검증)로 같은 경로를 탄다.

**Tech Stack:** Python 3.12, yfinance, SQLite, React/TS(Vite, Vitest).

## 결정 사항 (사용자 확인 2026-11-19)

| 항목 | 값 |
|---|---|
| 안전자산 비율 | 공통 1개 설정 `safe_weights` = (단기채, 장기채, 금), 합 100 |
| 위험자산 비율 | 시장별 `us_risk_weights` = (개별주, 나스닥, S&P500), `kr_risk_weights` = (개별주, 코스피, 코스닥), 각 합 100 |
| 지수 ETF | 레버리지·인버스 제외, 1배 추종 중 60일 평균 거래량·거래대금 1위: `QQQ`, `SPY`, `069500.KS`(KODEX 200), `229200.KS`(KODEX 코스닥150) |
| 금 ETF | 60일 평균 거래대금 1위: `GLD`, `411060.KS`(ACE KRX금현물, 2021-12-15 상장) |
| Config 기본값 | 동작 보존: `safe_weights=(100,0,0)`, `us_risk_weights=kr_risk_weights=(100,0,0)` (기존 `short_bond_pct=100` 과 동일) |
| 병합 후 운용 설정(지시자가 `control.update`) | `safe_weights=[80,0,20]`, `us_risk_weights=kr_risk_weights=[60,20,20]` |

## Global Constraints

- 브랜치 `feat/etf-sleeves`(worktree `.worktrees/etf`). main 병합은 지시자가 한 번에. 단계 하나 = 커밋 하나(커밋은 지시자가).
- Python 은 메인 체크아웃의 venv: `/data/workspace/github/market-cap-rebalancer/.venv/bin/python`. 작업 디렉터리는 worktree.
- 커밋 전 `.venv/bin/python -m pytest -q` 통과. 프런트 변경 시 `frontend/` 에서 `npm run lint && npm run typecheck && npm test -- --run && npm run build` 모두 0. `api.py` 변경 시 `scripts/export_openapi.py` → `npm run gen:api` 재생성.
- `mcr.db`, `.env` 를 열거나 쓰지 않는다. 테스트는 네트워크 금지(`FakeSource` 식 주입).
- 검증 상수(오래된 시세 40분, 주식수 ±20%, 시총 ±40%)는 바꾸지 않는다. ETF 는 지금 채권처럼 시총 검증 대상이 아니다.
- 시각은 `exchange_calendars` 로만. 전략 수치를 코드 상수로 박지 않는다.
- 주석·문서 언어는 주변을 따른다(설정 필드 설명 한국어, docstring 영어).

## Review Focus

1. **비율 계산**: 기본값(100/0/0)에서 기존 `pocket_weights` 결과와 완전히 같아야 한다. → `test_pocket_weights_defaults_match_old_rule`.
2. **개별주 데이터 없음**(`stock_w` 빈 dict): 개별주 몫은 같은 시장 지수 ETF 비율로 재정규화. 지수 비율도 0 이면 위험자산 몫 전체가 안전자산으로(기존 동작). → `test_pocket_weights_without_stocks_*`.
3. **백테스트 상장 전 구간**(411060.KS 2021-12-15 이전 등): 그날 가격 없는 ETF 는 빼고 같은 그룹(위험/안전) 안에서 재정규화. 안전자산 그룹이 통째로 없으면 단기채로. 실운용은 시세 없으면 지금처럼 `JobError`(대체 없음). → `test_pocket_weights_drops_unavailable_etf`, 백테스트 스모크.
4. **이관**: 저장된 `short_bond_pct=p` → `safe_weights=[p, 100-p, 0]`, 키 삭제, 이력 기록, `config:version` 3. v2→v3 는 `rank:since` 를 건드리지 않는다. v1 DB 는 v1→v2→v3 연속. 새 DB 는 바로 3. → `test_migrate_v2_to_v3_*`.
5. **정리 대상(`retired`)**: 금·지수 ETF 종목을 바꾸면 이전 종목이 ETF 로 정리 매도된다. 기존 `retired` 의 `"bond"` 역할도 ETF 로 계속 인식. → `test_changing_gold_ticker_retires_old_as_etf`.
6. **겹침 검증**: 시장별 ETF 5개(단기채·장기채·금·지수 2)는 서로 다르고 개별주 후보와 겹치지 않는다. 주간 풀 갱신이 겹치는 목록을 내면 기존 실패 경로(이전 풀 유지·알림)로 간다.

---

### Task A: 전략·설정·실운용·백테스트 (한 커밋, AGENTS.md "전략 규칙 변경" 규칙)

**Files:** Modify `strategy.py`, `control.py`, `jobs.py`, `backtest.py`, `report.py`/`webreport.py`(채권 표기가 있으면), `README.md`(규칙 표·설정 설명), `results/summary.md`(재생성); Tests `tests/test_strategy.py`, `tests/test_control.py`, `tests/test_jobs.py`, 필요 시 `tests/test_backtest.py`.

**Interfaces (Produces):**
- `Config`:
  - 삭제 `short_bond_pct`. 추가 `safe_weights: tuple[float, ...] = (100.0, 0.0, 0.0)` # 안전자산 비율 (단기채/장기채/금, 합 100)
  - `us_risk_weights`, `kr_risk_weights: tuple[float, ...] = (100.0, 0.0, 0.0)` # 위험자산 비율 (개별주/지수1/지수2, 합 100)
  - `us_gold="GLD"`, `kr_gold="411060.KS"`, `us_nasdaq_etf="QQQ"`, `us_sp500_etf="SPY"`, `kr_kospi_etf="069500.KS"`, `kr_kosdaq_etf="229200.KS"`
  - 메서드: `safe_assets(market) -> (short, long, gold)`, `index_etfs(market) -> (지수1, 지수2)`, `etfs(market) -> safe_assets + index_etfs`, `risk_weights(market)`. `bonds()` 는 없애고 호출부를 의미에 맞게 바꾼다.
- `pocket_weights(stock_w, market, cfg, available: Collection[str] | None = None) -> dict[str, float]`: 위 Review Focus 1~3 규칙. `available=None` 이면 모든 ETF 사용 가능. 합이 1 을 넘지 않는다. 0 인 항목은 결과에서 뺀다.
- 모듈 docstring 의 규칙 설명 갱신.
- `control.LIMITS`: `weights` 타입에 `"slots"`(rank_weights 5, 나머지 3) 추가, `validate_config` 는 `limit["slots"]` 로 길이 검사(`RANK_SLOTS` 상수 대신). 새 항목 라벨: `safe_weights` "안전자산 비율 (단기채/장기채/금, 합 100)", `us_risk_weights` "미국 위험자산 비율 (개별주/나스닥/S&P500, 합 100)", `kr_risk_weights` "한국 위험자산 비율 (개별주/코스피/코스닥, 합 100)", `us_gold` "미국 금 ETF", `kr_gold` "한국 금 ETF", `us_nasdaq_etf` "미국 나스닥 ETF", `us_sp500_etf` "미국 S&P500 ETF", `kr_kospi_etf` "한국 코스피 ETF", `kr_kosdaq_etf` "한국 코스닥 ETF"(모두 `ticker` 타입, 기존 패턴). `stock_pct` 라벨은 "위험자산 비중 (%)" 로(키는 유지).
- `control.roles`: 개별주 `"stock"`, ETF 7종 `"etf"`. 역할이 `"stock"` 이 아니면 ETF 로 취급(옛 `"bond"` 포함). `check_universe`: Review Focus 6.
- `control.migrate` / `CONFIG_VERSION = 3`: Review Focus 4.
- `jobs.py`: signal 의 시세 조회·`no fresh quote` 검사·snapshot 행, execute 의 `allowed`·`etfs`, 정리(`keep`) 가 채권 2개 대신 `cfg.etfs(market)` 전체를 쓴다. 메시지 문구 "bond ETF" → "ETF".
- `backtest.py`: 데이터 `extra` 에 양 시장 `etfs()` 전체. `pocket_weights(..., available=그날 가격 있는 종목)`. CLI: `--short-bond-pct` 를 `--safe-weights "80,0,20"`, `--us-risk-weights`, `--kr-risk-weights` 로 바꾸고 새 종목 인자 추가. 시나리오 추가: "안전자산 단기채 80 / 금 20", "위험자산 60/20/20 (지수 ETF)", "운용 설정 (일간, 이동 5%, 허용 3%p, 대기 1일, 위험 60/20/20, 안전 80/0/20)". 기본 기간(5년)으로 다시 돌려 `results/summary.md` 갱신.

- [ ] 실패 테스트 먼저(Review Focus 1~6 이름 그대로), 구현, `pytest -q` 통과.
- [ ] `PYTHONWARNINGS=ignore .venv/bin/python backtest.py` 실행 → `results/summary.md` 갱신, README 규칙 표·설정 설명 갱신.
- [ ] 커밋 `Add gold and index ETFs to the safe and risk sleeves`(본문: 결정 표, 백테스트 핵심 수치).

### Task B: 웹 설정 화면 (Task A 뒤)

**Files:** Modify `api.py`(`Limit` 에 `slots: int | None`), `frontend/openapi.json`, `frontend/src/api/schema.d.ts`(생성물), `frontend/src/components/StrategyPanel.tsx`, `frontend/src/App.test.tsx`, `frontend/src/test/helpers.ts`.
- weights 편집기를 `limit.slots` 칸 수로 일반화. 칸 이름: `rank_weights` 는 1~5위, 3칸 항목은 라벨 괄호 안 이름(예: 단기채/장기채/금)을 칸 이름으로. 합 100 검사 유지.
- 새 ticker 항목 6개가 기존 ticker 편집기로 보이는지.
- [ ] Vitest: `safe weights edits three slots summing to 100`, `risk weights rejects sum not 100`. 프런트 4종 0. 커밋 `Edit the safe and risk sleeve weights in the web UI`.

### Task C: 병합·적용 (지시자)

- [ ] main 병합(fast-forward), `frontend` 빌드(`npm run build`, `dist/` 는 git 밖).
- [ ] `[사용자]` `sudo systemctl restart mcr-web` (api.py 변경 반영). tick 은 5분마다 새 프로세스라 자동 반영, 첫 실행에서 `migrate` v3.
- [ ] `control.update(conn, {"config": {"safe_weights": [80, 0, 20], "us_risk_weights": [60, 20, 20], "kr_risk_weights": [60, 20, 20]}}, "cli")`.
- [ ] 병합 시각은 signal/execute/report 시각(KST 01:45·02:45·08:00·11:00·12:00 전후 10분)을 피한다.
