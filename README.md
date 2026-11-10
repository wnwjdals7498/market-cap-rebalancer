# market-cap-rebalancer

미국 / 한국 시총 상위 5종목을 순위별 비율로 담고 단기/장기 채권 ETF 를 섞는 전략. 5년 백테스트 + 매일 자동 실행 루틴(1단계: yfinance 데이터, 모의 계좌, SQLite 저장).

> 리눅스 서버 이전 중: 현재 상태와 다음 할 일은 [HANDOFF.md](HANDOFF.md).

## 규칙 (포켓 = 달러 계좌 / 원화 계좌 각각)

| 항목 | 기본값 | 내용 |
|---|---|---|
| 후보 종목 | 미국 AAPL NVDA MSFT GOOGL AMZN / 한국 삼성전자 SK하이닉스 SK스퀘어 LG에너지솔루션 현대차 | 시장별 주식 5종목 고정 목록. 웹·CLI 에서 바꿈 |
| 순위 | 매일 | 시총(현재가 × 주식수) 순으로 줄 세움. 새 순위는 `confirm_days`(기본 0 = 당일) 거래일 연속 유지돼야 확정 |
| 순위별 비율 | 50 / 30 / 20 / 0 / 0 | 주식 몫 안에서 1~5위에 나눔 (`rank_weights`, 합 100). 데이터 없는 종목은 순위에서 빠지고 남은 비율을 다시 정규화. 100/0/0/0/0 = 1위 단독 |
| 주식 : 채권 | 60 : 40 | `stock_pct` |
| 채권 안 | 단기 100 / 장기 0 | `short_bond_pct`. 미국 SHY / TLT, 한국 국고채3년 `114260.KS` / 국고채30년 `385560.KS` |
| 매매 주기 | 주 1회 | `trade_every` = 일간/주간/월간. 조사(signal)는 매일 하고 순위·대기 상태를 갱신하지만, 매매는 그 기간의 첫 성공 거래일에만. 첫 거래일이 실패하면 같은 기간 다음 거래일에 다시 시도 |
| 매매 1회 이동 상한 | 포켓의 10% | `step_pct`. 한 번 매매에서 매도 합계와 매수 합계가 각각 포켓 자산의 10% 이하. 순위 비율·주식/채권·단기/장기 조정 모두 같은 상한. 예외: 포켓에 전략 보유가 하나도 없을 때의 첫 매수는 한 번에 |
| 미국 : 한국 | 60 : 40 | `us_pct`. 총자산(원화 환산)의 미국 비중이 이 값에서 `fx_band_pp`(5%p) 넘게 벗어나면 환전 안내 (아래) |
| 정리 대상 | | 후보·채권 목록에서 빠진 종목은 "정리 대상"으로 남아 목표 0 으로 이동 상한 안에서 팔리고, 수량이 0 이 되면 목록에서 지워짐 |

백테스트 코드와 실운용은 같은 함수(`strategy.py`)를 쓴다. 전략 관리 범위는 종목 단위: 후보·채권·정리 대상은 계좌 수량 전부가 전략 몫이고, 그 밖의 종목은 읽기만 하고 매매하지 않는다.

### 미국/한국 비율과 환전 (수동)

KIS Open API 에 환전 주문이 없어서 환전은 자동으로 하지 않는다. 일일 보고(08:00 KST)가 비율을 보고, 밴드를 벗어났으면 환전 계획(방향·금액 = 목표까지의 차이와 넘치는 쪽 포켓의 10% 중 작은 값)을 만들어 텔레그램으로 알린다.

1. 넘치는 쪽 포켓은 다음 매매일에 그 금액만큼 팔아 현금으로 둔다.
2. 다음 보고에서 현금이 모였으면 "미국 포켓 현금 $X 를 원화로 환전하세요" 안내를 한 번 보낸다 (이후엔 일일 보고에 한 줄씩).
3. 사용자가 환전하면 받는 쪽 포켓이 다음 매매일에 목표 비율대로 산다.
4. `fx_wait_days`(3일) 안에 환전하지 않으면 현금을 다시 투자하고 `fx_enabled` 를 꺼서 환전 계획을 멈춘다 (팔고 다시 사는 반복 방지). 비율이 밴드 안으로 돌아오면 계획을 지운다.

모의투자 계좌에서 환전할 수 있는지는 확인되지 않았다. 안 되면 안내만 쌓이다가 3일 뒤 계획이 꺼진다.

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
.venv/Scripts/python backtest.py --rank-weights 100,0,0,0,0 --trade-every day
```

최근 결과 (2021-11-10 ~ 2026-11-10, 초기자본 1억원, 기본 설정, 비용 반영. 전체 표는 [results/summary.md](results/summary.md)):

| 시나리오 | 총수익률 | CAGR | 변동성 | MDD | 비용합계 |
|---|---|---|---|---|---|
| 전략 (설정값: 50/30/20, 주간) | +115.5% | +16.6% | 13.9% | -21.8% | 1,445,880원 |
| 1위 단독 (100/0/0/0/0) | +81.8% | +12.7% | 14.4% | -22.3% | 1,065,233원 |
| 매매 일간 | +111.9% | +16.2% | 14.0% | -22.1% | 2,795,352원 |
| 매매 월간 | +109.8% | +16.0% | 13.9% | -23.0% | 767,465원 |
| 같은 60/40, 지수 (SPY / KODEX200) | +75.6% | +11.9% | 9.9% | -19.7% | 294,743원 |
| SPY 단순보유 (원화환산) | +93.7% | +14.1% | 16.1% | -17.7% | |
| KODEX200 단순보유 | +132.1% | +18.3% | 26.6% | -46.9% | |

주의: 후보 종목은 **지금 시점의 시총 상위 5개**로 고정했다 (한국 SK스퀘어는 2021-11 상장이라 그 전엔 순위에서 빠짐). 5년 전에는 알 수 없던 선택이라 결과가 낙관적으로 나올 수 있다. 미국/한국 비율 환전은 안내 다음 날 사용자가 바로 환전한다고 가정하고 스프레드(0.25%)를 뗐다 (`fx_wait_days` 는 쓰지 않음).

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

외부 리눅스 서버에서 24시간 돌리려면 [deploy/DEPLOY.md](deploy/DEPLOY.md) (systemd 타이머 + 웹 서버, `sudo deploy/install.sh`).

### signal 이 하는 일
1. 발행주식수 하루 1회 갱신 (`impliedSharesOutstanding`, 전체 주식 클래스). 전일 대비 ±20% 넘게 변하면 거부.
2. 1분봉 현재가 조회. 오늘 장의 40분 이내 시세만 사용 (yfinance 한국 시세는 약 20분 지연).
3. 시총 = 현재가 × 주식수 → 순위. 시세·주식수가 없는 후보는 그날 순위에서 뺀다. 전일 대비 ±40% 넘게 변한 종목이 있으면 그날 순위 갱신 보류(`SKIPPED`), 전날 확정 순위 유지.
4. 확정 순위 갱신(`rank_state`). 매매 주기에 해당하는 날이면 순위 비율대로 목표를 만들되 매매 1회 이동 상한(10%)을 적용해 목표 비중 저장. 아닌 날은 순위만 기록하고 `no trade (week)` 로 끝낸다.

### execute (모의)
그 세션의 목표가 없으면(매매 주기 아님) `SKIPPED` / `매매 주기 아님` 으로 끝낸다 (알림 없음). 목표 비중 × 현재 평가액 → 정수 주. 매도 먼저, 포켓 0.5% 미만 주문 생략(전량 매도는 예외). 수수료·거래세 반영, 체결가 = 현재가.

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
- 현금이 모자라 덜 산 매수(실제 수수료가 모델보다 큼, 사다리 위쪽 체결)는 `cash-limited` 로 표시하고 OK 처리. 가격이 안 맞아 못 산·못 판 것만 PARTIAL. KIS 모의투자 미국 수수료는 약 1% (2026-11-03 확인).
- 계좌의 다른 종목(후보·채권 ETF·정리 대상 밖)은 건드리지 않음. 현금은 해당 통화 계좌 현금 전체를 사용.
- 단계별 주문은 `orders` 에 한 줄씩 (`step`, `broker_order_no`, `status` FILLED/PARTIAL/CANCELLED).

## 텔레그램 알림

1. 텔레그램 `@BotFather` 에서 `/newbot` → 봇 토큰을 `.env` 의 `TELEGRAM_BOT_TOKEN` 에 입력
2. 만든 봇에게 아무 메시지나 보낸 뒤 `.venv/Scripts/python notify.py --chat-ids` → 나온 값을 `TELEGRAM_CHAT_ID` 에 입력
3. `.venv/Scripts/python notify.py --test` 로 확인

| 알림 | 시점 |
|---|---|
| ❌ FAILED | signal/execute 실패. 같은 오류는 세션당 한 번만 (tick 이 5분마다 재시도해도). KIS 오류코드는 설명 첨부 (예: `40910000` 모의계좌 만료) |
| ⚠️ SKIPPED / PARTIAL | 데이터 이상으로 순위 갱신 보류(전날 확정 순위 유지), 일부 미체결 (매매 주기 아님은 알리지 않음) |
| ✅ execute | 주문이 있었던 날의 체결 내역 (DRY_RUN 계획 포함) |
| 🔄 순위 변화 | 확정 순위가 바뀜 |
| 💱 환전 | 환전 계획 생성 / 환전하세요 안내 / 환전 완료 확인 / 대기 초과(현금 재투자, 환전 안내 꺼짐) / 비율 회복 |
| ⚙️ 설정 이관 | 업데이트 후 첫 실행에서 저장된 설정을 한 번 옮김 |

설정이 비어 있으면 알림은 꺼짐. 전송 실패는 로그에만 남고 매매 작업을 멈추지 않음.

### 일일 보고 (매일 08:00 KST, `tick` 이 전송)

한국장 시작 전, 미국장 마감 후 시점. 직전 보고 이후 실행된 작업이 없으면(주말 등) 건너뜀. SQLite 기록만 쓰고 증권사는 호출하지 않음 (환율만 yfinance).

```
📊 일일 보고 2026-11-03 (화)
총자산 ₩96,883,760 (직전 +0.42%, 누적 +1.20%)
  원화 ₩49,992,510 · 달러 $35,000.00 (환율 1,339.8)

🇰🇷 KR 2026-11-03 | 순위 삼성전자 > SK하이닉스 > SK스퀘어 | 다음 매매 주간 | 대기 0/0일
  평가 ₩49,992,510
  보유 삼성전자 29.9%(목표 30%), SK하이닉스 18.0%(목표 18%), SK스퀘어 12.0%(목표 12%), 국고채3년 40.0%(목표 40%), 현금 0.1%
  체결(2026-11-03) 매수 삼성전자 68주 @218,500 / 매수 SK하이닉스 ...
  순위 삼성전자 1,277조 > SK하이닉스 1,014조 > SK스퀘어 95조 (1·2위 차 +26.0%)

🇺🇸 US ...

  💱 환전 계획: 미국 포켓 $9,000.00 → 원화 (생성 2026-11-03, 다음 매매일에 현금 확보)   (환전 계획이 있는 동안 매일 한 줄)

✅ 이상 없음            (또는 ⚠️ 확인 필요: FAILED/SKIPPED/PARTIAL/RUNNING 작업 목록)
```

- 평가는 마지막 보유 스냅샷(체결 시점 가격) 기준. 입출금이 있으면 직전/누적 수익률이 그만큼 왜곡됨.
- 미리보기: `jobs.py report --print`, 즉시 전송: `jobs.py report --force`.

## 웹 리포트 (실시간 현황)

열어 둔 페이지는 5초마다 SQLite 변경을 확인해서, 작업(signal/execute/report)이 DB 에 기록하면 새로고침 없이 바로 갱신된다. 스크롤 위치와 펼친 표는 유지. 제목 옆 배지가 상태를 보여 줌: `실시간 · 12:00:05 확인` / `서버 연결 끊김 · 재시도 중`(서버가 다시 뜨면 스스로 복구).

같은 서버가 설정 화면(http://127.0.0.1:8765/app/)과 설정 API(`/api/v1`)도 제공한다. 준비와 사용법은 아래 [웹 설정](#웹-설정-브라우저에서-설정-바꾸기) 절.

### 여는 방법

**1. 항상 켜두기 (권장)** — 로그온할 때 자동 시작, 죽으면 1분 뒤 재시작, 창 없음:

```bash
powershell -ExecutionPolicy Bypass -File scripts/register_web.ps1
```

등록 즉시 서버가 뜬다. 브라우저에서 http://127.0.0.1:8765/ 를 열어 두면 끝 (즐겨찾기 추천). 끄기/삭제:

```bash
powershell -Command "Stop-ScheduledTask MarketCapRebalancerWeb"
powershell -ExecutionPolicy Bypass -File scripts/register_web.ps1 -Unregister
```

**2. 필요할 때만** — 터미널을 연 동안만 켜짐 (Ctrl+C 로 종료):

```bash
.venv/Scripts/python jobs.py web --serve
```

**3. 파일로만** — `site/index.html` 생성 (외부 파일 없는 단일 HTML, 자동 갱신 없음). 매일 08:00 보고 때도 갱신됨:

```bash
.venv/Scripts/python jobs.py web
```

- 다른 DB 를 보려면 `jobs.py --db <경로> web --serve --port 8766` (포트를 바꾸면 동시에 여러 개).
- 여러 주소에서 함께 들으려면 `--host` 를 반복한다: `--host 127.0.0.1 --host 10.8.0.1` (사설망 주소, 기본은 `127.0.0.1` 하나).
- 이미 켜진 포트로 또 실행하면 `cannot listen on 127.0.0.1:8765 ... already running?` 메시지로 종료. 그냥 브라우저로 열면 됨.
- 리포트는 DB 를 읽기 전용으로 열어 매매 작업의 쓰기를 막지 않는다. 설정 API 는 DB 에 설정을 기록한다.

외부 서버에서는 `mcr-web.service` 가 같은 서버를 띄우고, SSH 터널이나 사설망(VPN)으로 접속한다: [deploy/DEPLOY.md](deploy/DEPLOY.md) 5번.

### 화면 구성

- 총자산·직전/누적 변화·환율 카드, 총자산 추이 차트(일일 보고가 쌓는 `equity` 기록), 시장별 보유 vs 목표 비중(머리글에 확정 순위·다음 매매 주기·대기 일수), 최근 체결, 시총 상위 3 추이, 주문 내역, 작업 기록.
- 차트: 마우스를 올리면 날짜별 값, `표로 보기` 로 숫자 표. 라이트/다크 모드 자동.
- 평가 금액은 마지막 signal/execute 시점 가격 기준 (서버가 시세를 따로 조회하지 않음).

### 보안

기본은 이 PC 에서만 접속 (`127.0.0.1`). 리포트에는 인증이 없으므로 `--host 0.0.0.0` 이나 공인 IP 로 열지 않는다. 설정 API 는 토큰이 없으면 동작하지 않는다.

## 웹 설정 (브라우저에서 설정 바꾸기)

`jobs.py config` 대신 브라우저에서 broker·주문 전송(submit)·시장별 일시정지·전략 파라미터(순위별 비율, 채권 비율, 매매 주기, 이동 상한, 미국/한국 비율, 환전 설정, 후보 종목·채권 ETF)를 보고 바꾼다. 화면은 `/api/v1` 의 한 클라이언트(React + Vite + TypeScript)다.

**준비 (한 번, 서버/PC 마다)** — Node `^20.19.0 || >=22.12.0` 이 필요하다 (빌드할 때만. 실행 중에는 필요 없다).

```bash
cd frontend && npm ci && npm run build      # frontend/dist/ 생성 (git 에 넣지 않는 생성물)
```

1. `.env` 에 `MCR_API_TOKEN` 을 넣는다 (`.env.example` 의 생성 방법 참고). 비어 있으면 API 는 503 으로 닫힌다.
2. `jobs.py web --serve` 를 띄우고 http://127.0.0.1:8765/app/ 을 연다. 처음 한 번 토큰을 입력한다 — 이 탭의 `sessionStorage` 에만 보관되고 탭을 닫으면 지워진다.
3. 화면 위에서 아래로: 상태(시장별 오늘 signal/execute 시각 KST, 일시정지, 시장별 시총 순위·확정 순위·목표 비중·마지막 매매일, 환전 계획, 최근 실행) → 운용(broker, 주문 전송, 일시정지) → 전략 파라미터 → 변경 이력.

- **반영 시점 = 다음 tick (최대 5분 뒤).** 웹은 SQLite `settings` 만 바꾼다. 매매는 지금처럼 `tick` 이 하고, 웹 서버가 죽어도 돈다.
- 주문 전송을 켤 때는 대화상자에 `SUBMIT` 을 직접 입력해야 한다. `allow_real` 은 웹에서 읽기 전용이다 (`jobs.py config --allow-real` 로만).
- 전략 파라미터를 바꾸면 `results/summary.md` 의 백테스트(기본값 기준)와 비교할 수 없다.
- 순위별 비율은 5칸 합이 100 이어야 저장된다. 후보 종목은 시장별 5개(중복 없음, 한국 `123456.KS`, 미국 대문자 티커), 채권 2종은 서로 다르고 후보와 겹치지 않아야 한다. 후보·채권에서 빠진 종목은 정리 대상이 되어 이동 상한 안에서 팔린다.
- 업데이트 후 첫 실행에서 저장된 옛 설정을 한 번 옮긴다 (`settings_log` 출처 `migration`, 텔레그램 1회): 순위 변경 대기일 0, 미국 비중 60, 없어진 항목 삭제, 옛 후보·채권은 정리 대상.
- 모든 변경은 변경 이력에 남고 텔레그램으로 알린다 (CLI 변경도 같다).

개발:

```bash
cd frontend
npm run dev                                  # Vite 개발 서버 (/api 는 127.0.0.1:8765 로 프록시)
npm run lint && npm run typecheck && npm test -- --run && npm run build
# API 가 바뀌면 타입 다시 생성
(cd .. && .venv/bin/python scripts/export_openapi.py) && npm run gen:api
```

## SQLite (`mcr.db`)

| 테이블 | 내용 |
|---|---|
| `settings` | 전략 설정·운용 설정(trading, paused), 정리 대상(`retired`), 환전 계획(`fx:plan`), 설정 버전(`config:version`) (JSON) |
| `settings_log` | 설정 변경 이력 (웹·CLI, 이전 값·새 값·출처) |
| `shares` | 일별 발행주식수 |
| `snapshots` | signal 시점 현재가·주식수·시총·검증 플래그 |
| `rankings` | 시장별 시총 순위 |
| `rank_state` | 시장·세션별 확정 순위, 후보 순위, 연속 일수 (옛 `leader_state` 는 더 이상 읽지 않고 테이블만 남음) |
| `targets` | 세션별 목표 비중 + 기준가 |
| `positions` | 보유 스냅샷 (CASH 포함) |
| `orders` | 주문/체결 기록 (2단계 실계좌도 같은 테이블) |
| `equity` | 일일 보고 시점 총자산 (원화·달러 포켓, 환율) — 웹 차트용 |
| `runs` | 작업 실행 기록 (`RUNNING/OK/SKIPPED/FAILED`) |

## 가정 / 한계

- 데이터: KIS 시세 (인증정보 있을 때) + yfinance (미국 주식수, 대체용). 새 소스는 `sources.py` 의 `Source` 인터페이스로 추가.
- 백테스트는 전일 종가 신호 / 당일 종가 체결, 실운용은 당일 11시 신호 / 12시 체결이라 결과가 조금 다를 수 있음.
- 비용: 미국 0.07%, 국내 0.015% + 주식 매도 거래세 0.20%, 환전 0.25%. **양도소득세·배당세 미반영**.
- 연속조회(페이지 넘김)는 미구현: 잔고·체결 목록이 한 페이지(국내 50건 등)를 넘으면 누락될 수 있음.
