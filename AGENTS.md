# AGENTS.md

이 저장소에서 작업하는 에이전트(사람 포함)용 작업 규칙. 전략·사용법은 [README.md](README.md).

## 작업 범위

- 이 폴더(`market-cap-rebalancer/`) 안에서만 파일을 만들고 고친다. 상위 디렉터리는 다른 프로젝트다.
- 패키지는 `.venv` 에만 설치하고 `requirements.txt` 에 추가한다. 전역 Python 에 설치하지 않는다.

## 커밋

- 구현 단계 하나 = 커밋 하나. 여러 단계를 한 커밋에 섞지 않는다 — 단계별로 되돌리고 리뷰할 수 있어야 한다.
- 커밋 전 `.venv/Scripts/python -m pytest` 통과 필수.
- 메시지: 영어 명령형 제목(50자 안팎) + 본문에 무엇을/왜. 기존 `git log` 형식을 따른다.
- `git status` 로 의도하지 않은 파일(`.omc/`, `mcr.db`, `logs/`, `cache/`, `results/*.csv`)이 없는지 확인. 생성물은 `.gitignore` 에 둔다.
- push 는 사용자가 요청할 때만.

## 실행 환경

```bash
.venv/Scripts/python -m pytest                 # 테스트
.venv/Scripts/python backtest.py               # 백테스트 (cache/ 에 데이터 캐시)
.venv/Scripts/python jobs.py --db <임시경로> ... # 수동 실험은 임시 DB 로. 실사용 mcr.db 를 건드리지 않는다
```

- yfinance/pandas 경고가 많다. 출력 볼 때 `PYTHONWARNINGS=ignore PYTHONIOENCODING=utf-8`.
- 테스트는 네트워크를 쓰지 않는다. `tests/conftest.py` 가 텔레그램 전송을 막는다 (`.env` 에 봇이 설정돼 있어도). 외부 데이터가 필요한 로직은 `tests/test_jobs.py` 의 `FakeSource` 처럼 `Source` 를 주입해 검증한다.

## 코드 구조와 규칙

| 파일 | 역할 |
|---|---|
| `strategy.py` | 전략 규칙. 백테스트와 일일 루틴이 **같은 함수**를 쓴다 |
| `backtest.py`, `data.py` | 과거 데이터 백테스트 |
| `sources.py` | 실시간 데이터 소스 (`Source` 인터페이스) |
| `kis_auth.py`, `kis_api.py` | KIS 인증(.env, 토큰 캐시), REST 클라이언트(호출 간격·재시도), `KisSource` |
| `brokers.py`, `execution.py` | `Broker` 인터페이스·`SimBroker`, 지정가 사다리(±5%)·호가단위 |
| `kis_broker.py` | KIS 잔고·매수가능·주문·체결조회·취소 (`KisBroker`) |
| `markets.py` | 시장 정의, 세션별 signal/execute 시각 |
| `db.py` | SQLite 스키마와 접근 함수 |
| `notify.py` | 텔레그램 알림 (설정 없으면 꺼짐, 예외를 밖으로 던지지 않음) |
| `report.py` | 일일 보고 텍스트 (SQLite 만 읽음) |
| `webreport.py` | 웹 리포트 (단일 HTML, 인라인 SVG 차트, 로컬 서버: 읽기 전용, `/version` 폴링으로 자동 갱신) |
| `jobs.py` | CLI: init / signal / execute / tick / status / config / report / web |
| `scripts/`, `deploy/` | 자동 실행 등록: Windows 작업 스케줄러 / Linux systemd (`deploy/DEPLOY.md`) |

- 전략 규칙을 바꾸면 `strategy.py` 만 고치고, 백테스트를 다시 돌려 `results/summary.md` 갱신, README 규칙 표 갱신까지 같은 커밋에. 백테스트와 실운용 규칙이 갈라지면 백테스트 결과가 의미를 잃는다.
- 전략 수치(비율, 일수, 수수료)는 `Config` 필드로 추가한다. 코드에 상수로 박지 않는다.
- 시각은 `exchange_calendars` 로만 계산한다. KST/ET 고정 시각이나 서머타임 분기를 직접 쓰지 않는다.
- 새 데이터 소스(KRX, SEC, KIS 시세 등)는 `Source` 를 구현해 추가한다. `jobs.py` 에 소스별 분기를 넣지 않는다.
- 데이터 검증(오래된 시세 40분, 주식수 ±20%, 시총 ±40%)은 완화하지 않는다. 바꿔야 하면 이유를 커밋 본문에 쓴다. 잘못된 시총 하나가 1위 교체 → 실제 매매로 이어진다.
- `runs` 테이블의 1세션 1회 실행 보장을 우회하지 않는다. 같은 날 두 번 돌면 3일 확정 카운트가 두 번 올라간다.
- 실행 방식(CLI 인자, 실행 주기, 시간 제한, 웹 포트)을 바꾸면 Windows 스크립트와 `deploy/systemd/*`, `deploy/DEPLOY.md` 를 같이 고친다. 서버로 옮길 때 그대로 적용돼야 한다.
- 주석·문서 밀도와 언어는 주변 코드를 따른다 (설정 필드 설명은 한국어, docstring 은 영어).

## 금지 / 사용자 확인 필요

- 실계좌 주문 코드(2단계, KIS Open API)는 기본 `DRY_RUN`, 모의투자 서버부터. 실전 주문을 보내는 실행은 사용자 확인 없이 하지 않는다.
- API 키·시크릿·계좌번호를 코드, 커밋, SQLite, 로그에 넣지 않는다. 환경변수 또는 OS 자격 증명 저장소를 쓴다.
- 스케줄러·서비스 등록(Windows `scripts/register_*.ps1`, Linux `deploy/install.sh`) 등 PC·서버 설정 변경은 사용자 확인 후.
- `mcr.db`(실사용 모의 계좌 기록)를 지우거나 덮어쓰지 않는다.
