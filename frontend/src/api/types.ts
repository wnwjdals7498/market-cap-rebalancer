/**
 * 화면이 쓰는 API 타입.
 *
 * 서버(api.py)가 응답 모델을 선언하지 않아 생성 타입(schema.d.ts)의 응답은 `unknown` 이고
 * 요청 본문의 필드도 `{[key: string]: unknown}` 이다. 그래서 화면에 필요한 최소 타입을 여기서
 * 좁혀 쓰고, 생성 타입과 어긋나면 컴파일이 깨지도록 아래 두 곳에서 묶어 둔다:
 *  - API_PATHS: 경로 문자열이 생성된 paths 에 있어야 한다.
 *  - SettingsPatch: 생성된 요청 본문 스키마에 대입 가능해야 한다.
 * 서버가 응답 모델을 선언하면 이 파일의 응답 타입을 생성 타입으로 바꾼다.
 */
import type { components, paths } from './schema'

export const API_PATHS = {
  status: '/api/v1/status',
  settings: '/api/v1/settings',
  log: '/api/v1/settings/log',
  orders: '/api/v1/orders',
} as const satisfies Record<string, keyof paths>

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

/** PATCH /api/v1/settings 응답: control.update() 의 snapshot, limits 는 없다. */
export type Snapshot = Omit<Settings, 'limits'> & Partial<Pick<Settings, 'limits'>>

/** PATCH /api/v1/settings 본문 */
export type SettingsPatch = { // interface 는 인덱스 시그니처(생성 타입)에 대입되지 않아 type 으로 둔다
  trading?: Partial<Pick<Trading, 'broker' | 'submit'>>
  paused?: Partial<Paused>
  config?: Record<string, number>
  confirm?: string
}

// 좁힌 요청 타입이 서버 스키마(생성 타입)에 대입 가능해야 한다.
export const patchMatchesSchema: SettingsPatch extends components['schemas']['SettingsPatch'] ? true : never = true

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
