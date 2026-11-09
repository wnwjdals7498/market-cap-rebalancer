import { useCallback, useEffect, useMemo, useState } from 'react'
import { UnauthorizedError, createClient, tokenStore, type Client } from './api/client'
import type { Settings, Snapshot } from './api/types'
import { ChangeLog } from './components/ChangeLog'
import { StatusPanel } from './components/StatusPanel'
import { StrategyPanel } from './components/StrategyPanel'
import { TokenGate } from './components/TokenGate'
import { TradingPanel } from './components/TradingPanel'

function Dashboard({ client, onUnauthorized }: { client: Client; onUnauthorized: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    client
      .getSettings()
      .then((s) => alive && setSettings(s))
      .catch((e: unknown) => {
        if (!alive) return
        if (e instanceof UnauthorizedError) onUnauthorized()
        else setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      alive = false
    }
  }, [client, onUnauthorized])

  // PATCH 응답에 limits 가 없으면 기존 값을 유지한다.
  const apply = useCallback((next: Snapshot) => {
    setSettings((prev) => (prev ? { ...prev, ...next, limits: next.limits ?? prev.limits } : prev))
  }, [])

  return (
    <main>
      <header>
        <h1>MCR 설정</h1>
        <nav>
          <a href="/">투자 리포트</a>
          <a href="/api/docs">API 문서</a>
        </nav>
      </header>
      {error && <p role="alert" className="error">{error}</p>}
      <StatusPanel client={client} onUnauthorized={onUnauthorized} />
      {settings ? (
        <>
          <TradingPanel client={client} settings={settings} onChange={apply} onUnauthorized={onUnauthorized} />
          <StrategyPanel client={client} settings={settings} onChange={apply} onUnauthorized={onUnauthorized} />
        </>
      ) : (
        !error && <p>불러오는 중…</p>
      )}
      <ChangeLog client={client} refreshKey={settings} onUnauthorized={onUnauthorized} />
    </main>
  )
}

export default function App() {
  const [token, setToken] = useState<string | null>(() => tokenStore.get())
  const client = useMemo(() => (token ? createClient(token) : null), [token])

  const logout = useCallback(() => {
    tokenStore.clear()
    setToken(null)
  }, [])

  if (!client) {
    return (
      <TokenGate
        onSubmit={(t) => {
          tokenStore.set(t)
          setToken(t)
        }}
      />
    )
  }
  return <Dashboard client={client} onUnauthorized={logout} />
}
