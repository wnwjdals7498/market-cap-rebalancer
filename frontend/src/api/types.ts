/**
 * 화면이 쓰는 API 타입. 서버 스키마와 같은 모양은 다시 쓰지 않고 생성 타입(schema.d.ts)의 별칭으로 둔다.
 * 서버가 바뀌면 `python scripts/export_openapi.py && npm run gen:api` 로 갱신하고 컴파일 오류를 고친다.
 */
import type { components, paths } from './schema'

type Schemas = components['schemas']

export const API_PATHS = {
  status: '/api/v1/status',
  settings: '/api/v1/settings',
  log: '/api/v1/settings/log',
  orders: '/api/v1/orders',
} as const satisfies Record<string, keyof paths>

export type Trading = Schemas['Trading']
export type Paused = Schemas['Paused']
export type Limit = Schemas['Limit']
export type Settings = Schemas['Settings']
export type Snapshot = Schemas['Snapshot']
export type SettingsPatch = Schemas['SettingsPatch']
export type MarketTimes = Schemas['MarketTimes']
export type Run = Schemas['Run']
export type Status = Schemas['Status']
export type MarketStrategy = Schemas['MarketStrategy']
export type FxPlan = Schemas['FxPlan']
export type ConfigValue = Settings['config'][string]
export type LogEntry = Schemas['LogEntry']
export type Order = Schemas['Order']

/** 화면 좁힘: 서버는 markets 를 문자열 키 맵으로 내지만 시장은 Paused 의 키(KR, US)다. */
export type MarketCode = keyof Paused
