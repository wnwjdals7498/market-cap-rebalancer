/**
 * 임시 타입: 계획서의 API (v1) 표를 보고 손으로 썼다.
 * 5b 에서 `npm run gen:api` (openapi-typescript) 생성물로 교체한다.
 */

export type MarketCode = 'KR' | 'US'

export interface Trading {
  broker: 'sim' | 'kis'
  submit: boolean
  allow_real: boolean
}

export type Paused = Record<MarketCode, boolean>

/** control.snapshot() 의 config: Config 전체 필드(kr_taxable 제외). */
export type StrategyConfig = Record<string, number | string>

export interface Limit {
  type: 'int' | 'float'
  min: number
  max: number
}

/** GET /api/v1/settings */
export interface Settings {
  trading: Trading
  paused: Paused
  config: StrategyConfig
  editable: string[]
  limits: Record<string, Limit>
}

/** PATCH /api/v1/settings 응답: limits 는 없을 수 있다. */
export type Snapshot = Omit<Settings, 'limits'> & Partial<Pick<Settings, 'limits'>>

/** PATCH /api/v1/settings 본문 */
export interface SettingsPatch {
  trading?: Partial<Pick<Trading, 'broker' | 'submit'>>
  paused?: Partial<Paused>
  config?: Record<string, number>
  confirm?: string
}

export interface MarketTimes {
  session: string | null
  signal: string | null
  execute: string | null
  close: string | null
}

export interface Run {
  job: string
  market: string
  session_date: string
  started_at: string
  finished_at: string | null
  status: string
  detail: string | null
}

/** GET /api/v1/status */
export interface Status {
  trading: Trading
  paused: Paused
  markets: Record<MarketCode, MarketTimes>
  runs: Run[]
  db_version: string
}

/** GET /api/v1/settings/log (old/new 는 JSON 문자열) */
export interface LogEntry {
  id: number
  at: string
  key: string
  old: string | null
  new: string | null
  source: string
}

/** GET /api/v1/orders (orders 테이블 행) */
export type Order = Record<string, string | number | null>
