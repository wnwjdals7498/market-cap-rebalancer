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

## KIS Open API 인증 (2단계 준비)

`.env.example` 을 `.env` 로 복사해 값 입력 (`.env` 는 git-ignore). 계좌번호는 `12345678-01` 또는 8자리(상품코드 01 가정).

```bash
.venv/Scripts/python kis_auth.py    # 토큰 발급/재사용 확인. 키·토큰 값은 출력하지 않음
```

접근토큰은 `%LOCALAPPDATA%\market-cap-rebalancer\` 에 캐시하고 만료 1시간 전까지 재사용 (KIS 토큰 발급 제한 대응).

### 시세 소스 (`jobs.py --source auto|kis|yfinance`, 기본 auto)

- `auto`: `.env` 인증정보가 있으면 KIS, 없으면 yfinance.
- KIS: 현재가는 한국·미국 모두 KIS. 주식수는 한국 = KIS 상장주식수, 미국 = yfinance (KIS 는 상장 클래스만 줘서 GOOGL 시총이 절반으로 잡힘).
- KIS 에서 실패한 종목은 yfinance 로 대체. snapshot 의 `source` 열에 실제 출처 기록.
- KIS 응답엔 시각이 없어서 장중이면 조회 시각, 장 밖이면 직전 장 마감 시각으로 기록 → 장 밖 시세는 자동으로 오래된 시세 처리.
- 호출 간격: 모의 1.05초, 실전 0.06초. `EGW00201`(초당 건수 초과)은 재시도.
- 주식수 급변 검사는 같은 소스끼리만 비교 (보통주만 vs 전체 클래스 차이로 오탐 방지).

## 주문 (2단계)

```bash
.venv/Scripts/python jobs.py config --broker kis              # KIS 잔고 사용, 주문은 기록만 (DRY_RUN)
.venv/Scripts/python jobs.py config --submit                  # 모의투자 서버로 실제 주문 전송
.venv/Scripts/python jobs.py config --broker sim --no-submit  # SQLite 모의 체결로 복귀
```

| 설정 | 동작 |
|---|---|
| `broker=sim` (기본) | SQLite 보유로 즉시 모의 체결 |
| `broker=kis`, `submit=false` | 잔고는 KIS 계좌에서 읽고, 주문은 `orders` 에 `PLANNED` 로만 기록 |
| `broker=kis`, `submit=true` | KIS 로 지정가 주문 전송 |
| `KIS_ENV=real` | `--allow-real` 없으면 실행 거부 |

- 주문은 정규장 지정가만 (`ORD_DVSN=00`, 한국은 `EXCG_ID_DVSN_CD=KRX`). 미국은 KIS 모의/실전 모두 매수 시장가가 없음.
- 가격 사다리: 기준가(주문 시점 현재가) 대비 0 → 0.5 → 1 → 2 → 3.5 → 5%. 단계마다 60초 대기, 미체결분 취소 후 다음 가격. 5% 를 넘지 않음. 호가단위(KRX, ETF 5원, 미국 1센트)에 맞춤.
- 매도 먼저, 매수는 매수가능수량(한국 미수 없는 수량, 미국 달러 주문가능) 이내.
- 계좌의 다른 종목(전략 universe·채권 ETF 밖)은 건드리지 않음. 현금은 해당 통화 계좌 현금 전체를 사용.
- 단계별 주문은 `orders` 에 한 줄씩 (`step`, `broker_order_no`, `status` FILLED/PARTIAL/CANCELLED).

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

- 데이터: KIS 시세 (인증정보 있을 때) + yfinance (미국 주식수, 대체용). 새 소스는 `sources.py` 의 `Source` 인터페이스로 추가.
- 백테스트는 전일 종가 신호 / 당일 종가 체결, 실운용은 당일 11시 신호 / 12시 체결이라 결과가 조금 다를 수 있음.
- 비용: 미국 0.07%, 국내 0.015% + 주식 매도 거래세 0.20%, 환전 0.25%. **양도소득세·배당세 미반영**.
- 연속조회(페이지 넘김)는 미구현: 잔고·체결 목록이 한 페이지(국내 50건 등)를 넘으면 누락될 수 있음.
