import { useEffect, useState } from 'react'
import { UnauthorizedError, type Client } from '../api/client'
import type { LogEntry } from '../api/schema'
import { kst, showJson } from '../format'

interface Props {
  client: Client
  /** 값이 바뀌면 다시 읽는다 (설정 저장 뒤). */
  refreshKey: unknown
  onUnauthorized: () => void
}

export function ChangeLog({ client, refreshKey, onUnauthorized }: Props) {
  const [rows, setRows] = useState<LogEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    client
      .getLog(50)
      .then((r) => {
        if (!alive) return
        setRows(r)
        setError(null)
      })
      .catch((e: unknown) => {
        if (!alive) return
        if (e instanceof UnauthorizedError) onUnauthorized()
        else setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      alive = false
    }
  }, [client, refreshKey, onUnauthorized])

  return (
    <section className="card">
      <h2>변경 이력</h2>
      {error && <p role="alert" className="error">{error}</p>}
      {rows && rows.length === 0 && <p className="hint">변경 기록 없음</p>}
      {rows && rows.length > 0 && (
        <table>
          <thead>
            <tr><th>시각 (KST)</th><th>항목</th><th>이전</th><th>이후</th><th>출처</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{kst(r.at)}</td>
                <td>{r.key}</td>
                <td>{showJson(r.old)}</td>
                <td>{showJson(r.new)}</td>
                <td>{r.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
