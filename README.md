# market-cap-rebalancer

미국 / 한국 시총 1위 + 채권 ETF 비율 유지 전략. 백테스트 + 매일 실행용 모의 리밸런서.

## 규칙 (포켓 = 달러 계좌 / 원화 계좌 각각)

| 상황 | 목표 |
|---|---|
| 평상시 | 시총 1위 60 / 채권 ETF 40 |
| 1위 변경 | 하루 최대 포켓 자산 10%씩 구 1위 → 신 1위 이동. 확정 전에 뒤집히면 10%씩 되돌림 |
| 신 1위 3일 연속 유지 | 1위 30 / 2위 30 / 채권 40 (SPLIT) — 이후 1·2위 순서만 바뀌면 거래 없음 |
| SPLIT 중 보유 2종목 밖 종목이 3일 연속 top2 | 밀려난 종목 → 신규 종목 10%씩 이동 |
| 주식 : 채권 비율 | 한도 없이 매일 맞춤 |

모든 수치는 옵션: `--stock-pct --us-pct --step-pct --confirm-days --pair-confirm-days --solo-after-days --band-pp --fx-mode --us-bond --kr-bond`.

## 사용

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python backtest.py                       # 최근 5년, 결과 results/
.venv/Scripts/python rebalance.py --init-usd 35000 --init-krw 50000000 --apply
.venv/Scripts/python rebalance.py --apply              # 매일 1회
```

## 가정 / 한계

- 데이터: yfinance. 시총 = 종가 × 발행주식수(분할 보정). 삼성전자는 보통주만.
- 신호는 전일 종가 순위, 체결은 당일 종가 (look-ahead 없음). 소수점 주식 가정(백테스트).
- 비용: 미국 0.07%, 국내 0.015% + 주식 매도 거래세 0.20%, 환전 0.25%. **양도소득세·배당세 미반영** (매일 리밸런싱은 해외주식 양도세 22% 를 매년 발생시킴).
- `rebalance.py` 는 모의 계좌(`paper_state.json`). 실계좌는 `Broker` 인터페이스 구현 필요 (예: 한국투자증권 Open API).
