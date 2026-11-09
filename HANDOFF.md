# HANDOFF: 리눅스 서버로 이전

Windows PC 에서 만든 market-cap-rebalancer 를 리눅스 서버에서 24시간 돌리기 위한 인수인계 문서.
서버의 작업자(사람 또는 Claude Code)는 이 파일 → [AGENTS.md](AGENTS.md) → [deploy/DEPLOY.md](deploy/DEPLOY.md) 순서로 읽고 시작한다.

작성 기준: 2026-11-09, `main` 의 `0609f66` 다음 커밋 (이 파일을 더한 커밋).

## 1. 지금 상태

| 항목 | 상태 |
|---|---|
| 1단계 (데이터·신호·SQLite) | 완료. yfinance + KIS 시세, 거래 1시간 전 signal, 정규장 중간 execute |
| 2단계 (KIS 주문·잔고) | 모의투자 서버에서 동작. 기본 DRY_RUN, `config --submit` 이면 주문 전송, 실전은 `--allow-real` 필요 |
| 알림 / 리포트 | 텔레그램 실패·체결 알림, 08:00 KST 일일 보고, 웹 리포트(SQLite 변경 시 자동 갱신) |
| 배포 파일 | `deploy/install.sh` + systemd 유닛. WSL Ubuntu 22.04 에서만 검증, 실제 서버 설치는 처음 |
| PC 쪽 자동 실행 | **등록된 적 없음** (작업 스케줄러 없음). 끌 것 없음 |
| 실사용 DB `mcr.db` | **PC 에 없음.** 서버에서 새로 만든다 (DB 옮기기 단계 생략) |
| 테스트 | 53개 통과 (Windows Python 3.13, WSL Python 3.10) |

### KIS 모의계좌에서 확인된 것 (2026-11-03 미국장)
- US 매수(`VTTT1002U`)·매도(`VTTT1001U`) 주문, 체결 조회, 미체결 취소 후 재가격 주문, 매수가능금액 한도.
- 체결된 주문을 취소하면 `40430000 취소수량 초과` 가 오며 정상 처리됨 (로그만 남김).
- **모의투자 미국 수수료는 약 1%** (모델은 0.07%). 그래서 마지막 매수가 현금 부족으로 몇 주 덜 사지는 게 정상이고, `cash-limited` 로 표시하고 OK 처리한다.
- 모의계좌에는 그 테스트로 산 미국 보유분(NVDA, IEF)이 남아 있다. `broker=kis` 면 보유를 계좌에서 읽으므로 그대로 이어서 리밸런싱된다.

### 아직 확인 안 된 것
- **한국장 실제 주문 전송**: KR 은 시세·signal·DRY_RUN 까지만 확인. 서버의 첫 KR execute(12:00 KST)가 첫 실주문 검증이다.
- 리눅스 실서버에서 systemd 타이머로 도는 것, 해외 리전 IP 에서 KIS 접속 가능 여부.

## 2. 서버에서 할 일 (순서대로)

`[사용자]` 표시는 작업자가 대신할 수 없거나, AGENTS.md 상 사용자 확인이 필요한 단계다.

1. **코드·환경**: DEPLOY.md 1~2번 (apt, clone, venv, pytest 통과).
2. `[사용자]` **`.env` 전달**: 사용자가 PC 에서 `scp .env user@SERVER:~/market-cap-rebalancer/.env`. 작업자는 내용을 열거나 출력하지 않는다. 존재와 권한만 확인하고 `chmod 600 .env`.
3. **DB 새로 만들기**: `mcr.db` 가 PC 에 없으므로 새로 시작한다.
   ```bash
   .venv/bin/python jobs.py init --usd 0 --krw 0       # 전략 설정 저장. 금액은 broker=sim 일 때만 쓰임
   .venv/bin/python jobs.py config --broker kis        # KIS 계좌 연동, 아직 DRY_RUN (주문 안 나감)
   chmod 600 mcr.db
   ```
4. **연결 확인**: `kis_auth.py` (token ok paper), `notify.py --test`, `jobs.py status`.
5. **DRY_RUN 으로 하루 관찰 (권장)**: 설치 후 첫 세션은 주문 없이 `orders` 에 `PLANNED` 만 쌓이는지, 계획 수량이 계좌 잔고와 맞는지 본다.
6. `[사용자]` **서비스 설치**: `sudo deploy/install.sh` (서버 설정 변경이므로 사용자 확인 후).
7. `[사용자]` **주문 전송 켜기**: 5번 결과를 사용자에게 보여 주고 승인받은 뒤 `jobs.py config --broker kis --submit` (모의투자). **실전(`KIS_ENV=real`, `--allow-real`)은 이번 범위 밖.**
8. **웹 접속 안내**: 사용자 PC 에서 `ssh -N -L 8765:127.0.0.1:8765 user@SERVER` → http://127.0.0.1:8765/.
9. **첫 세션 지켜보기**: 아래 시각에 `journalctl -u mcr-tick -n 50`, `jobs.py status`, 텔레그램 알림 확인.

| 세션 | signal | execute |
|---|---|---|
| KR | 11:00 KST | 12:00 KST |
| US | 정규장 중간 1시간 전 (보통 11:45 ET) | 보통 12:45 ET (KST 새벽 2:45, 서머타임 해제 기준) |
| 일일 보고 | 08:00 KST | |

## 3. 지켜야 할 것 (AGENTS.md 요약)

- API 키·시크릿·계좌번호·텔레그램 토큰을 출력·커밋·로그·채팅에 넣지 않는다. `.env` 는 git-ignore.
- 실전 주문, 스케줄러/서비스 등록은 사용자 확인 후. 기본은 DRY_RUN → 모의 `--submit` 순.
- `mcr.db` 를 지우거나 덮어쓰지 않는다. 실험은 `jobs.py --db /tmp/xxx.db ...`.
- PC 와 서버가 동시에 `tick` 을 돌리면 주문이 두 번 나간다. 서버로 옮긴 뒤 PC 에서 스케줄러를 등록하지 않는다.
- 구현 단계 하나 = 커밋 하나, 커밋 전 pytest. push 는 사용자가 요청할 때만.

## 4. 남은 작업 (우선순위 순)

1. **execute 시작 시 남은 미체결 주문 정리**: 프로세스가 사다리 도중 죽으면 KIS 에 미체결 지정가가 남을 수 있다. 다음 execute 전에 당일 미체결을 조회·취소.
2. **KR 실주문 결과 반영**: 첫 KR `--submit` 체결 후 `inquire-daily-ccld` 응답 필드, 호가단위(ETF 5원) 동작 확인, 문제 있으면 수정.
3. **모의 수수료 반영**: `Config.us_fee` 를 계좌 실제 요율에 맞추는 옵션 (모의 1%, 실전은 증권사 요율). 지금은 `cash-limited` 로 흡수 중.
4. **실전 전환 준비**: 시장별 투자 한도(계좌 전체가 아닌 일부만 운용), 실전 앱키 별도 `.env`.
5. 잔고/체결 조회 페이지네이션 (보유 종목이 많아질 때. 현재 universe 규모에서는 불필요).

## 5. 막혔을 때

| 증상 | 확인 |
|---|---|
| 텔레그램 `FAILED ... 40910000` | 모의계좌 기간 만료 → 사용자가 KIS 에서 재신청, `.env` 교체 |
| `EGW00201` 반복 | 초당 호출 한도. 모의는 1.05초 간격, 다른 프로그램이 같은 앱키를 쓰는지 |
| tick 이 안 돎 | `systemctl list-timers mcr-tick.timer`, `journalctl -u mcr-tick` |
| `RUNNING` 이 남음 | 60분 지나면 자동 재시도 (`db.STALE_RUN`) |
| 웹 페이지 "서버 연결 끊김" | `systemctl status mcr-web`, SSH 터널이 살아 있는지 |
