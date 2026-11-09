import { useEffect, useState } from 'react'
import { UnauthorizedError, type Client } from '../api/client'
import type { MarketCode, Status } from '../api/types'
import { kst } from '../format'

const REFRESH_MS = 30_000

export function StatusPanel({ client, onUnauthorized }: { client: Client; onUnauthorized: () => void }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const s = await client.getStatus()
        if (!alive) return
        setStatus(s)
        setError(null)
      } catch (e) {
        if (!alive) return
        if (e instanceof UnauthorizedError) onUnauthorized()
        else setError(e instanceof Error ? e.message : String(e))
      }
    }
    void load()
    const timer = setInterval(() => void load(), REFRESH_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [client, onUnauthorized])

  return (
    <section className="card">
      <h2>상태 <span className="hint">(KST, 30초마다 갱신)</span></h2>
      {error && <p role="alert" className="error">{error}</p>}
      {!status && !error && <p>불러오는 중…</p>}
      {status && (
        <>
          <table>
            <thead>
              <tr><th>시장</th><th>세션</th><th>signal</th><th>execute</th><th>상태</th></tr>
            </thead>
            <tbody>
              {Object.entries(status.markets).map(([code, m]) => {
                return (
                  <tr key={code}>
                    <td>{code}</td>
                    <td>{m.session ?? '오늘 없음'}</td>
                    <td>{kst(m.signal)}</td>
                    <td>{kst(m.execute)}</td>
                    <td>{status.paused[code as MarketCode] ? '일시정지' : '동작'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <h3>최근 실행</h3>
          {status.runs.length === 0 ? (
            <p className="hint">기록 없음</p>
          ) : (
            <table>
              <thead>
                <tr><th>작업</th><th>시장</th><th>세션</th><th>시작</th><th>결과</th><th>메모</th></tr>
              </thead>
              <tbody>
                {status.runs.map((r) => (
                  <tr key={`${r.job}-${r.market}-${r.session_date}`}>
                    <td>{r.job}</td>
                    <td>{r.market}</td>
                    <td>{r.session_date}</td>
                    <td>{kst(r.started_at)}</td>
                    <td>{r.status}</td>
                    <td>{r.detail ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  )
}
