import type { LogEntry, Order, Settings, SettingsPatch, Snapshot, Status } from './schema'

export class UnauthorizedError extends Error {
  constructor() {
    super('unauthorized')
    this.name = 'UnauthorizedError'
  }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
  }
}

function detailOf(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (detail !== undefined) return JSON.stringify(detail)
  return fallback
}

export function createClient(token: string) {
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await fetch(path, {
      ...init,
      headers: {
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        Authorization: `Bearer ${token}`,
      },
    })
    if (res.status === 401) throw new UnauthorizedError()
    const body: unknown = await res.json().catch(() => null)
    if (!res.ok) throw new ApiError(res.status, detailOf(body, `HTTP ${res.status}`))
    return body as T
  }

  return {
    getStatus: () => request<Status>('/api/v1/status'),
    getSettings: () => request<Settings>('/api/v1/settings'),
    patchSettings: (body: SettingsPatch) =>
      request<Snapshot>('/api/v1/settings', { method: 'PATCH', body: JSON.stringify(body) }),
    getLog: (limit = 50) => request<LogEntry[]>(`/api/v1/settings/log?limit=${limit}`),
    getOrders: (limit = 40) => request<Order[]>(`/api/v1/orders?limit=${limit}`),
  }
}

export type Client = ReturnType<typeof createClient>

const TOKEN_KEY = 'mcr_token'

/** 토큰은 sessionStorage 에만 둔다 (탭을 닫으면 사라진다). */
export const tokenStore = {
  get(): string | null {
    try {
      return sessionStorage.getItem(TOKEN_KEY)
    } catch {
      return null
    }
  },
  set(token: string) {
    try {
      sessionStorage.setItem(TOKEN_KEY, token)
    } catch {
      /* 저장 못 해도 이번 탭에서는 쓸 수 있다 */
    }
  },
  clear() {
    try {
      sessionStorage.removeItem(TOKEN_KEY)
    } catch {
      /* ignore */
    }
  },
}
