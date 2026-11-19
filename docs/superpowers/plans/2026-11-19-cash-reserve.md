# 투자금 / 보관 현금 분리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 시장별 계좌 현금 중 일부를 "보관 현금"으로 떼어 전략 매매에서 뺀다. 사용자가 "현금 X 만들기"를 하면 다음 매매에서 투자금을 팔아 마련하고, "투자금 전환"을 하면 다음 매매에서 투입한다. 입금·인출은 잔고 변화로 자동 감지해 보관 현금에 반영한다.

**Architecture:** 보관 현금은 `settings` 의 `cash` 키 `{"KR": 원, "US": 달러}` 한 곳에만 둔다. 실운용에서 브로커 잔고를 읽은 직후 `CASH -= 보관 현금` 으로 "투자금 현금"을 만들고, 그 뒤 로직(signal 의 매매 필요 판단, execute 의 주문 계획)은 그대로 쓴다. 전략 비율 규칙은 바뀌지 않으므로 백테스트는 바꾸지 않는다(`plan_moves` 는 새 인자 기본값 0 으로 기존 동작 유지).

**Tech Stack:** Python 3.12, SQLite, FastAPI, React/TS.

## 결정 사항 (사용자 확인 2026-11-19)

| 항목 | 값 |
|---|---|
| 명령 경로 | 웹 설정 화면 + CLI (`jobs.py cash`). 텔레그램 수신 없음 |
| 통화 | 시장별: KR 은 원, US 는 달러 |
| 현금화 속도 | 다음 execute 에서 한 번에. 이동 상한(`step_pct`)·매매 주기·허용 오차와 무관 |
| 입금·인출 | 자동 감지. 증가 = 입금 → 보관 현금 +, 감소 = 인출 → 보관 현금에서 먼저 빼고 모자라면 투자금 감소. 감지할 때마다 텔레그램 알림 |
| 시작 상태 | 보관 현금 0 (지금 계좌 돈은 전부 투자금) |

## 동작 정의

- **투자금 현금** `C = 계좌 현금 - R` (R = 그 시장 보관 현금). 음수일 수 있다(= 현금화해야 할 부족분).
- **현금 만들기 X**: `R += X`. 검증: X > 0, `R + X ≤ 그 시장 최근 평가 총액`(최근 snapshot 기준). `cash:pending[market] = true`.
- **투자금 전환 X**: `R -= X`. 검증: 0 < X ≤ R. `cash:pending[market] = true`.
- **직접 설정 X**: `R = X` (정정용, X ≥ 0). `cash:pending[market] = true`.
- 모든 변경은 `control.update` 와 같은 수준으로 이력(`settings_log`, 키 `cash.KR`/`cash.US`)·알림을 남긴다. 출처 `web`/`cli`/`system`(감지).
- **입출금 감지**: 잔고를 읽는 지점(signal 시작, execute 시작)마다 `계좌 현금 - 직전 관측 현금` 을 계산한다. 직전 관측은 "execute 직후 다시 읽은 잔고" 또는 "직전 signal/execute 시작 잔고" 중 마지막 것이다(그 사이에는 전략 매매가 없으므로 차이 = 외부 입출금). `|차이| ≥ flow_min`(시장별 설정, 기본 KR 10,000원, US 10달러)이면:
  - 입금(+d): `R += d`.
  - 인출(-d): `R -= min(R, d)` (모자라는 만큼은 투자금 감소로 둔다).
  - 이력 출처 `system`, 알림 "💵 KR 입금 감지 +X원 → 보관 현금 R원" / "인출 감지".
  - 최초 관측(직전 값 없음)은 기록만 한다.
  - 관측값은 `settings` 키 `cash:observed` `{"KR": {"cash": x, "at": iso}, ...}`.
- **execute**: 브로커 잔고 → `managed` → `CASH -= R`. `cash:pending` 이면 매매 주기·허용 오차와 무관하게 이번 execute 에서 매매한다. 주문 계획은 `plan_moves(..., cash_flow=|C 중 정산할 몫|)`: 부족분(C<0)은 매도로 전부, 전환 등으로 생긴 여유 현금(C>0)은 매수로 전부 정산하고, 자산 간 재조정 부분만 `step_pct` 상한을 받는다. execute 성공(OK/PARTIAL) 뒤 잔고를 다시 읽어 `cash:observed` 갱신, `cash:pending` 해제.
- **signal**: 잔고 → 입출금 감지 → `CASH -= R` 로 매매 필요 판단. `cash:pending` 이면 `trade_due`·`needs_moves` 와 무관하게 목표를 저장한다(“no trade” 아님).
- **보고·웹 리포트**: 시장별 "투자금 평가액"과 "보관 현금"을 따로 보여 준다. 수익 표시는 투자금 기준: `투자 원금` 을 `settings` 키 `cash:principal` 로 관리(시작 = 기능 첫 실행 시 투자금 평가액, 전환 +X, 현금 만들기 -X, 감지된 인출 중 투자금에서 빠진 몫 -), `투자 손익 = 투자금 평가액 - 투자 원금`.
- **수동 환전**: 한쪽 인출 + 다른 쪽 입금으로 감지된다. 받은 쪽에서 "투자금 전환"을 해야 투입된다(README 에 명시).

## Global Constraints

- 브랜치 `feat/cash-reserve`(worktree `.worktrees/cash`). 단계 하나 = 커밋 하나(커밋은 지시자). main 병합은 지시자.
- Python: `/data/workspace/github/market-cap-rebalancer/.venv/bin/python`. 커밋 전 `pytest -q` 통과, 프런트 변경 시 4종(`lint`, `typecheck`, `test -- --run`, `build`) 0, `api.py` 변경 시 `scripts/export_openapi.py` → `npm run gen:api`.
- `mcr.db`, `.env` 를 열거나 쓰지 않는다. 테스트는 네트워크 금지(`FakeSource`, fake broker).
- 검증 상수(40분, ±20%, ±40%)와 `runs` 1세션 1회 보장을 건드리지 않는다. 시각은 `exchange_calendars` 로만.
- 백테스트 결과(`results/summary.md`)가 바뀌면 안 된다(전략 규칙 불변). 바뀌면 이유를 보고한다.

## Review Focus

1. **부족분 정산**: R 을 늘리면 다음 execute 에서 매도 합계 ≥ 부족분, 이동 상한과 무관. → `test_reserve_increase_sells_deficit_at_next_execute`.
2. **전환 투입**: R 을 줄이면 다음 execute 에서 그 금액만큼 매수(정수 주·최소 주문 규칙 안에서). → `test_invest_conversion_buys_at_next_execute`.
3. **보관 현금 불가침**: 평소 매매에서 계좌 현금이 R 아래로 내려가는 매수 계획이 나오지 않는다. → `test_reserved_cash_is_never_spent`.
4. **입출금 감지**: 직전 관측 대비 증가 → R 증가, 감소 → R 먼저 감소, 문턱 미만 무시, 첫 관측 기록만, execute 직후 재관측으로 매매 대금이 입출금으로 잡히지 않음. → `test_deposit_detected_into_reserve`, `test_withdrawal_reduces_reserve_first`, `test_trade_cash_is_not_a_flow`, `test_small_change_below_flow_min_ignored`.
5. **명령 검증**: 전환 X > R 거부, 음수 거부, 현금 만들기 X 가 평가 총액 초과 거부, 이력·알림 남음. → `test_cash_commands_validate_and_log`.
6. **pending 강제 매매**: 주기 아님/허용 오차 안이어도 pending 이면 signal 이 목표 저장, execute 성공 후 해제, 실패하면 유지. → `test_pending_cash_forces_trade_and_clears`.
7. **기존 동작 보존**: R=0, pending 없음이면 기존 테스트 전부 그대로 통과, 백테스트 불변.

---

### Task A: 핵심 (control·jobs·strategy, 한 커밋)

**Files:** Modify `control.py`(cash 명령·검증·이력·알림, `flow_min` 설정), `jobs.py`(감지·차감·pending·재관측·CLI `cash`), `strategy.py`(`plan_moves(..., cash_flow: float = 0.0)`), `db.py`(필요 시); Tests `tests/test_jobs.py`, `tests/test_control.py`, `tests/test_strategy.py`.

**Interfaces (Produces):**
- `control.cash_state(conn) -> {"reserve": {"KR": float, "US": float}, "pending": {...}, "principal": {...}}`
- `control.cash_command(conn, market: str, action: Literal["reserve", "invest", "set"], amount: float, source: str) -> dict` — 위 검증, `ControlError` 로 거부.
- `control.record_flow(conn, market, delta, source="system")` — 감지 반영.
- `control.LIMITS` 에 `flow_min_krw`(float 0~10,000,000, 기본 10,000, "입출금 감지 최소 금액 (원)"), `flow_min_usd`(float 0~10,000, 기본 10, "입출금 감지 최소 금액 (달러)"). `Config` 필드로 추가(설명 한국어).
- CLI: `jobs.py cash --market KR|US (--reserve X | --invest X | --set X)`, 인자 없으면 현재 상태 출력. `jobs.py status` 에 시장별 보관 현금·pending 한 줄.
- `plan_moves(units, prices, weights, cfg, investable, cash_flow=0.0)`: 매도·매수 상한을 각각 `step_pct × 포켓 + cash_flow` 로. 기본 0 = 기존과 동일.

- [ ] 실패 테스트 먼저(Review Focus 1~7 이름), 구현, `pytest -q` 통과, `backtest.py` 다시 돌려 `results/summary.md` 수치 불변 확인(파일이 바뀌면 되돌리고 원인 보고).
- [ ] 커밋 `Keep a reserved cash balance outside the strategy`.

### Task B: API·웹 화면 (Task A 뒤)

**Files:** Modify `api.py`(`GET /api/v1/cash`, `POST /api/v1/cash` {market, action, amount}, 토큰 필요, 현금 만들기는 본문 `confirm: "CASH"` 필요), `frontend/openapi.json`, `frontend/src/api/schema.d.ts`(생성물), 새 컴포넌트 `frontend/src/components/CashPanel.tsx`, `App.tsx`, 테스트.
- 시장별 카드: 보관 현금, 투자금 평가액(최근 snapshot), 대기 중(pending) 표시, 입력란 + [현금 만들기] [투자금 전환] [직접 설정]. 현금 만들기는 확인 대화상자에 `CASH` 입력.
- [ ] pytest(`tests/test_api.py` 에 cash 엔드포인트), Vitest(`cash reserve requires CASH confirmation`, `invest rejects more than reserve`), 프런트 4종 0. 커밋 `Manage reserved cash from the web UI`.

### Task C: 보고·문서 (Task A 뒤, B 와 병렬 가능)

**Files:** Modify `report.py`(시장별 투자금/보관 현금/투자 손익 줄), `webreport.py`(타일에 보관 현금), `README.md`(새 절 "투자금 / 보관 현금": 명령, 감지 규칙, 수동 환전 시 전환 필요, 미국 결제 시점 주의), `AGENTS.md` 표(`jobs.py` 설명에 cash).
- [ ] pytest 통과. 커밋 `Report invested and reserved cash separately`.

### Task D: 병합·적용 (지시자)

- [ ] main 병합, `npm run build`, `[사용자]` `sudo systemctl restart mcr-web`.
- [ ] 첫 실행에서 보관 현금 0, 관측값 기록, 투자 원금 = 현재 투자금 평가액 확인.
- [ ] 미국 첫 매도 체결 뒤 `ord_psbl_frcr_amt` 가 매도 대금을 바로 반영하는지 확인(반영 안 되면 다음 날 입금으로 오감지 → 감지 대상에서 미결제 매도 대금을 빼는 보정 추가).
