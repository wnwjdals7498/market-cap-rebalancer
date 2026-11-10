import { vi } from 'vitest'
import type { Settings, Status } from '../api/types'

export const settings: Settings = {
  trading: { broker: 'sim', submit: false, allow_real: false },
  paused: { KR: false, US: false },
  config: {
    stock_pct: 60,
    us_pct: 60,
    band_pp: 0,
    step_pct: 10,
    confirm_days: 0,
    min_trade_pct: 0.5,
    us_fee: 0.0007,
    kr_fee: 0.00015,
    kr_stock_sell_tax: 0.002,
    fx_spread: 0.0025,
    rank_weights: [50, 30, 20, 0, 0],
    short_bond_pct: 100,
    trade_every: 'day',
    fx_band_pp: 5,
    fx_enabled: true,
    fx_wait_days: 3,
    pool_size: 15,
    pool_auto: true,
    us_universe: ['AAPL', 'NVDA', 'MSFT', 'GOOGL', 'AMZN'],
    kr_universe: ['005930.KS', '000660.KS', '402340.KS', '373220.KS', '005380.KS'],
    us_short_bond: 'SHY',
    us_long_bond: 'TLT',
    kr_short_bond: '114260.KS',
    kr_long_bond: '385560.KS',
  },
  editable: [
    'band_pp', 'confirm_days', 'fx_band_pp', 'fx_enabled', 'fx_wait_days', 'kr_fee', 'kr_long_bond',
    'kr_short_bond', 'kr_universe', 'min_trade_pct', 'pool_auto', 'pool_size', 'rank_weights', 'short_bond_pct', 'step_pct',
    'stock_pct', 'trade_every', 'us_fee', 'us_long_bond', 'us_pct', 'us_short_bond', 'us_universe',
  ],
  limits: {
    stock_pct: { type: 'float', label: '주식 비중 (%)', min: 0, max: 100 },
    us_pct: { type: 'float', label: '미국 포켓 비중 (%)', min: 0, max: 100 },
    band_pp: { type: 'float', label: '주식/채권 허용 오차 (%p)', min: 0, max: 50 },
    step_pct: { type: 'float', label: '매매 1회 최대 이동량', min: 1, max: 100 },
    confirm_days: { type: 'int', label: '순위 변경 대기일', min: 0, max: 30 },
    min_trade_pct: { type: 'float', label: '최소 주문 크기', min: 0, max: 10 },
    us_fee: { type: 'float', label: '미국 수수료율', min: 0, max: 0.05 },
    kr_fee: { type: 'float', label: '한국 수수료율', min: 0, max: 0.05 },
    rank_weights: { type: 'weights', label: '순위별 주식 비율', min: 0, max: 100 },
    short_bond_pct: { type: 'float', label: '단기채 비율', min: 0, max: 100 },
    trade_every: { type: 'choice', label: '매매 주기', options: ['day', 'week', 'month'] },
    fx_band_pp: { type: 'float', label: '환전 허용 오차', min: 0, max: 50 },
    fx_enabled: { type: 'bool', label: '환전 안내 사용' },
    fx_wait_days: { type: 'int', label: '환전 대기 일수', min: 1, max: 30 },
    pool_size: { type: 'int', label: '후보 풀 크기', min: 5, max: 30 },
    pool_auto: { type: 'bool', label: '후보 풀 매주 자동 갱신' },
    us_universe: { type: 'tickers', label: '미국 후보 풀 (자동 갱신)', market: 'US', min: 5, max: 30 },
    kr_universe: { type: 'tickers', label: '한국 후보 풀 (자동 갱신)', market: 'KR', min: 5, max: 30 },
    us_short_bond: { type: 'ticker', label: '미국 단기채', market: 'US' },
    us_long_bond: { type: 'ticker', label: '미국 장기채', market: 'US' },
    kr_short_bond: { type: 'ticker', label: '한국 단기채', market: 'KR' },
    kr_long_bond: { type: 'ticker', label: '한국 장기채', market: 'KR' },
  },
}

export const status: Status = {
  trading: settings.trading,
  paused: settings.paused,
  markets: {
    KR: {
      session: '2026-11-10',
      signal: '2026-11-09T16:45:00+00:00', // 2026-11-10 01:45 KST
      execute: '2026-11-10T05:50:00+00:00', // 14:50 KST
      close: '2026-11-10T06:30:00+00:00',
    },
    US: { session: null, signal: null, execute: null, close: null },
  },
  runs: [
    {
      job: 'signal', market: 'US', session_date: '2026-11-06',
      started_at: '2026-11-06T21:00:00+00:00', finished_at: '2026-11-06T21:00:03+00:00',
      status: 'OK', detail: null,
    },
  ],
  strategy: {
    KR: { ranking: [], confirmed: [], targets: {}, last_trade: null },
    US: {
      ranking: ['AAPL', 'NVDA', 'MSFT'],
      confirmed: ['AAPL', 'NVDA', 'MSFT'],
      targets: { AAPL: 0.3, SHY: 0.4 },
      last_trade: '2026-11-09',
    },
  },
  fx: null,
  db_version: '1',
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>

/** Replace global fetch. Keys are "METHOD /path" (query string ignored); unknown routes answer 404. */
export function stubFetch(routes: Record<string, Handler>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), 'http://localhost').pathname
    const handler = routes[`${init?.method ?? 'GET'} ${path}`]
    return handler ? handler(init) : json({ detail: `no route ${path}` }, 404)
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

export function defaultRoutes(): Record<string, Handler> {
  return {
    'GET /api/v1/settings': () => json(settings),
    'GET /api/v1/status': () => json(status),
    'GET /api/v1/settings/log': () => json([]),
  }
}

export function patchCalls(fn: ReturnType<typeof stubFetch>) {
  return fn.mock.calls.filter(([, init]) => init?.method === 'PATCH')
}
