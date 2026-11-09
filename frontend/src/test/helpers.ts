import { vi } from 'vitest'
import type { Settings, Status } from '../api/types'

export const settings: Settings = {
  trading: { broker: 'sim', submit: false, allow_real: false },
  paused: { KR: false, US: false },
  config: {
    stock_pct: 60,
    us_pct: 50,
    fx_mode: 'separate',
    band_pp: 0,
    step_pct: 10,
    confirm_days: 3,
    pair_confirm_days: 3,
    solo_after_days: 0,
    us_bond: 'IEF',
    kr_bond: '148070.KS',
    us_fee: 0.0007,
    kr_fee: 0.00015,
    kr_stock_sell_tax: 0.002,
    fx_spread: 0.0025,
    min_trade_pct: 0.5,
  },
  editable: [
    'band_pp', 'confirm_days', 'kr_fee', 'min_trade_pct', 'pair_confirm_days',
    'solo_after_days', 'step_pct', 'stock_pct', 'us_fee', 'us_pct',
  ],
  limits: {
    stock_pct: { type: 'float', min: 0, max: 100 },
    us_pct: { type: 'float', min: 0, max: 100 },
    band_pp: { type: 'float', min: 0, max: 50 },
    step_pct: { type: 'float', min: 1, max: 100 },
    confirm_days: { type: 'int', min: 0, max: 30 },
    pair_confirm_days: { type: 'int', min: 0, max: 30 },
    solo_after_days: { type: 'int', min: 0, max: 365 },
    min_trade_pct: { type: 'float', min: 0, max: 10 },
    us_fee: { type: 'float', min: 0, max: 0.05 },
    kr_fee: { type: 'float', min: 0, max: 0.05 },
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
