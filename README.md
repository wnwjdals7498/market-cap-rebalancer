# market-cap-rebalancer

미국 / 한국 시총 1위 + 채권 ETF 비율 유지 전략. 5년 백테스트 + 매일 자동 실행 루틴(1단계: yfinance 데이터, 모의 계좌, SQLite 저장).

## 규칙 (포켓 = 달러 계좌 / 원화 계좌 각각)

| 상황 | 목표 |
|---|---|
| 평상시 | 시총 1위 60 / 채권 ETF 40 |
| 1위 변경 | 하루 최대 포켓 자산 10%씩 구 1위 → 신 1위 이동. 확정 전에 뒤집히면 10%씩 되돌림 |
| 신 1위 3일 연속 유지 | 1위 30 / 2위 30 / 채권 40 (SPLIT) — 이후 1·2위 순서만 바뀌면 거래 없음 |
| SPLIT 중 보유 2종목 밖 종목이 3일 연속 top2 | 밀려난 종목 → 신규 종목 10%씩 이동 |
| 주식 : 채권 비율 | 한도 없이 매일 맞춤 |

채권 ETF: 미국 `IEF`, 한국 `148070.KS` (국고채10년). 비율·일수는 모두 설정값.

## 설치

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m pytest
```

## 백테스트

```bash
.venv/Scripts/python backtest.py                                  # 최근 5년, 결과 results/summary.md
.venv/Scripts/python backtest.py --stock-pct 70 --step-pct 20 --confirm-days 5
```

## 매일 루틴

| 시장 | 시총 조회 + 목표 계산 (`signal`) | 모의 체결 (`execute`) |
|---|---|---|
| 한국 | 11:00 KST | 12:00 KST |
| 미국 | 11:45 ET (00:45 / 01:45 KST) | 12:45 ET, 반일장은 장 중간 |

시각은 `exchange_calendars` 로 계산 (휴장일, 서머타임, 반일장, 지연 개장 반영).

```bash
.venv/Scripts/python jobs.py init --usd 35000 --krw 50000000      # 모의 계좌 + 설정 저장 (--stock-pct 등)
.venv/Scripts/python jobs.py tick                                  # 지금 할 일이 있으면 실행
.venv/Scripts/python jobs.py signal --market KR [--force]          # 수동 실행
.venv/Scripts/python jobs.py execute --market KR
.venv/Scripts/python jobs.py status
```

자동 실행: 5분마다 `tick` 을 돌리는 Windows 작업 스케줄러 등록 (PC 가 켜져 있어야 함).

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Unregister
```

### signal 이 하는 일
1. 발행주식수 하루 1회 갱신 (`impliedSharesOutstanding`, 전체 주식 클래스). 전일 대비 ±20% 넘게 변하면 거부.
2. 1분봉 현재가 조회. 오늘 장의 40분 이내 시세만 사용 (yfinance 한국 시세는 약 20분 지연).
3. 시총 = 현재가 × 주식수 → 순위. 전일 대비 ±40% 넘게 변한 종목이 있으면 그날 순위 갱신 보류(`SKIPPED`), 전날 비중 유지.
4. 1위/2위 상태 갱신 → 보유 기준으로 10% 이동 한도 적용한 목표 비중 저장.

### execute (모의)
목표 비중 × 현재 평가액 → 정수 주. 매도 먼저, 포켓 0.5% 미만 주문 생략(전량 매도는 예외). 수수료·거래세 반영, 체결가 = 현재가.

### 같은 날 다시 실행
`runs` 테이블이 시장·세션별로 한 번만 실행되게 막음. 실패(`FAILED`)한 작업은 다음 `tick` 에서 재시도, `--force` 는 전날 상태에서 다시 계산.

## SQLite (`mcr.db`)

| 테이블 | 내용 |
|---|---|
| `settings` | 전략 설정 (JSON) |
| `shares` | 일별 발행주식수 |
| `snapshots` | signal 시점 현재가·주식수·시총·검증 플래그 |
| `rankings` | 시장별 시총 순위 |
| `leader_state` | 1위/2위 상태 (모드, 연속 일수, 보유 2종목, 후보) |
| `targets` | 세션별 목표 비중 + 기준가 |
| `positions` | 보유 스냅샷 (CASH 포함) |
| `orders` | 주문/체결 기록 (2단계 실계좌도 같은 테이블) |
| `runs` | 작업 실행 기록 (`RUNNING/OK/SKIPPED/FAILED`) |

## 가정 / 한계

- 데이터: yfinance (비공식). `sources.py` 의 `Source` 인터페이스로 KIS 시세 API 등 교체 가능.
- 백테스트는 전일 종가 신호 / 당일 종가 체결, 실운용은 당일 11시 신호 / 12시 체결이라 결과가 조금 다를 수 있음.
- 비용: 미국 0.07%, 국내 0.015% + 주식 매도 거래세 0.20%, 환전 0.25%. **양도소득세·배당세 미반영**.
- 실계좌 주문(2단계: KIS Open API, 장 중간 ±5% 지정가)은 아직 없음.
