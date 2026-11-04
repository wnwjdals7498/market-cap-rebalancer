# 외부 서버 배포 (Linux + systemd)

지금 PC(Windows 작업 스케줄러)에서 돌리던 것을 리눅스 서버로 옮기는 절차. 위에서부터 순서대로 복사해 실행하면 된다.
Windows 에서 계속 돌릴 때의 방식은 [README](../README.md) 의 "매일 루틴"과 "웹 리포트" 절을 본다.

| 항목 | Windows (현재 PC) | Linux 서버 |
|---|---|---|
| 5분마다 `jobs.py tick` | `scripts/register_task.ps1` | `mcr-tick.timer` → `mcr-tick.service` |
| 웹 리포트 상시 실행 | `scripts/register_web.ps1` | `mcr-web.service` (127.0.0.1:8765) |
| 설치 / 제거 | 각 스크립트, `-Unregister` | `sudo deploy/install.sh`, `--uninstall` |
| 로그 | `logs/jobs.log` | `logs/jobs.log` + `journalctl -u mcr-tick` |
| KIS 토큰 캐시 | `%LOCALAPPDATA%\market-cap-rebalancer` | `~/.cache/market-cap-rebalancer` (권한 600) |

검증: WSL Ubuntu 22.04 (Python 3.10, systemd 249) 에서 테스트 전체 통과, `systemd-analyze verify` 통과, 웹 서버의 자동 갱신 확인.
실제 원격 서버에 설치해서 돌려 보지는 않았다.

## 0. 먼저: 한 곳에서만 돌린다

**같은 KIS 계좌로 PC 와 서버가 동시에 `tick` 을 돌리면 주문이 두 번 나간다.** DB 가 서로 달라 `runs` 의 중복 실행 방지가 작동하지 않는다.
서버를 켜기 전에 Windows 쪽 작업을 끈다 (PowerShell):

```powershell
Disable-ScheduledTask -TaskName MarketCapRebalancer          # 되돌리기: Enable-ScheduledTask
Disable-ScheduledTask -TaskName MarketCapRebalancerWeb       # 등록했었다면
```

작업이 도는 중에는 옮기지 않는다. 시각은 거래소 달력으로 계산된다: KR signal 11:00 / execute 12:00 KST, US 는 정규장 중간(보통 12:45 ET)과 그 1시간 전, 일일 보고 08:00 KST.
옮기기 직전에 `jobs.py status` 에 `RUNNING` 이 없는지 확인한다.

## 1. 서버 준비

- systemd 가 있는 리눅스 (Ubuntu 22.04+ 권장), Python 3.10 이상, git. 서버 시간대는 상관없다. 시각은 모두 거래소 달력과 UTC 로 계산한다.
- 밖으로 나가는 HTTPS 연결: KIS (`openapi.koreainvestment.com:9443`, 모의 `openapivts.koreainvestment.com:29443`), Yahoo Finance, Telegram.
- 들어오는 포트는 SSH 만 연다. 웹 리포트는 외부에 열지 않는다 (아래 5번).
- 해외 리전 IP 에서 KIS 접속이 제한되는지는 확인하지 않았다. 국내 리전을 권장.

```bash
sudo apt update && sudo apt install -y python3-venv git
```

## 2. 코드와 패키지

서비스는 이 checkout 을 그 자리에서 실행한다. 일반 사용자 계정으로 받는다 (root 금지).

```bash
git clone https://github.com/wnwjdals7498/market-cap-rebalancer.git ~/market-cap-rebalancer
cd ~/market-cap-rebalancer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # 전부 통과해야 다음 단계
```

## 3. 비밀값(.env)과 DB 옮기기

`.env` 와 `mcr.db` 는 git 에 없다. PC 에서 서버로 직접 복사한다 (Windows PowerShell 에는 `scp` 가 기본으로 있다).

**DB 를 이어서 쓸 때.** 3일 확정 카운트와 보유·주문 기록이 그대로 이어진다. 먼저 PC 에서 일관된 사본을 만든다:

```powershell
cd D:\workspace\test-space\github\market-cap-rebalancer
.venv\Scripts\python -c "import sqlite3; sqlite3.connect('mcr.db').backup(sqlite3.connect('mcr-transfer.db'))"
scp .env user@SERVER:~/market-cap-rebalancer/.env
scp mcr-transfer.db user@SERVER:~/market-cap-rebalancer/mcr.db
Remove-Item mcr-transfer.db
```

**새로 시작할 때.** `.env` 만 복사하고 서버에서 만든다:

```bash
.venv/bin/python jobs.py init --usd 35000 --krw 50000000      # 비율 등은 README 의 init 옵션
.venv/bin/python jobs.py config --broker kis --submit           # 모의투자 서버로 주문. 실전은 KIS_ENV=real + --allow-real
```

**서버에서 권한을 잠근다:**

```bash
chmod 600 .env mcr.db
```

## 4. 연결 확인 → 설치

```bash
.venv/bin/python kis_auth.py              # token ok (paper|real) ...
.venv/bin/python notify.py --test          # 텔레그램 "테스트 알림입니다."
.venv/bin/python jobs.py status            # 설정(broker/submit)과 최근 실행 기록

deploy/install.sh --render /tmp/mcr-units  # (선택) 채워질 유닛 파일 미리 보기
sudo deploy/install.sh                     # 설치 + 시작. 웹 서버 빼려면: sudo WITH_WEB=0 deploy/install.sh
```

`install.sh` 가 하는 일:
- `deploy/systemd/*` 의 `@REPO@`(checkout 경로)와 `@USER@`(sudo 를 실행한 사용자)를 채워 `/etc/systemd/system/` 에 넣는다.
- `mcr-tick.timer` 와 `mcr-web.service` 를 enable 하고 바로 시작한다.
- `.venv` 가 없거나 root 로 돌리려 하면 거부한다. `.env` 권한이 600 이 아니면 경고한다.

## 5. 웹 리포트 접속

서버는 `127.0.0.1:8765` 에서만 듣는다. 페이지에 로그인 기능이 없으므로 **`0.0.0.0` 이나 공인 IP 로 열지 않는다.**

**SSH 터널 (기본).** PC 에서 실행해 두고 브라우저로 http://127.0.0.1:8765/ 를 연다. 자동 갱신도 터널을 그대로 통과한다.

```bash
ssh -N -L 8765:127.0.0.1:8765 user@SERVER
```

**Tailscale 같은 사설망.** 서버의 사설망 IP 로만 듣게 바꾼다:

```bash
sudo systemctl edit mcr-web.service
#   [Service]
#   ExecStart=
#   ExecStart=/home/USER/market-cap-rebalancer/.venv/bin/python jobs.py web --serve --host 100.x.y.z --port 8765
sudo systemctl restart mcr-web.service
```

인터넷에 꼭 공개해야 한다면 리버스 프록시(Caddy, nginx)에 HTTPS 와 로그인(basic auth 등)을 붙이고, 서버 자체는 계속 127.0.0.1 에 둔다.

## 6. 운영

| 확인할 것 | 명령 |
|---|---|
| 다음 tick 시각 | `systemctl list-timers mcr-tick.timer` |
| 방금 tick 결과 | `journalctl -u mcr-tick -n 50 --no-pager` |
| 작업 기록 / 설정 | `.venv/bin/python jobs.py status` |
| 전체 로그 | `tail -f logs/jobs.log` |
| 웹 서버 상태 | `systemctl status mcr-web` |
| 지금 tick 하나 실행 | `sudo systemctl start mcr-tick.service` |
| 잠시 멈춤 / 재개 | `sudo systemctl stop mcr-tick.timer` / `start` |
| 완전 제거 | `sudo deploy/install.sh --uninstall` (코드, .env, mcr.db 는 남음) |

**코드 업데이트.** tick 이 도는 중이 아닐 때 한다. `systemctl is-active mcr-tick.service` 가 `inactive` 이면 된다.
tick 은 다음 실행부터 새 코드를 쓰고, 웹 서버만 재시작하면 된다.

```bash
git pull && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m pytest -q \
  && sudo systemctl restart mcr-web.service
```

`deploy/systemd/*` 가 바뀌었으면 `sudo deploy/install.sh` 를 다시 실행한다.

**백업** (`*.db` 는 git-ignore). 매매가 없는 시간에 하루 한 번 (`crontab -e`, 서버 시간대 기준):

```cron
30 21 * * * cd $HOME/market-cap-rebalancer && mkdir -p backups && .venv/bin/python -c "import sqlite3,datetime; sqlite3.connect('mcr.db').backup(sqlite3.connect(f'backups/mcr-{datetime.date.today()}.db'))"
```

위 예시는 서버가 UTC 일 때 06:30 KST 다 (US 장 마감 뒤, 08:00 보고 전).

## 되돌리기 (서버 → PC)

```bash
sudo systemctl disable --now mcr-tick.timer mcr-web.service     # 서버 정지 먼저
```

그다음 3번과 같은 방법으로 `mcr.db` 사본을 PC 로 가져오고, PC 에서 `Enable-ScheduledTask MarketCapRebalancer` 를 실행한다.
