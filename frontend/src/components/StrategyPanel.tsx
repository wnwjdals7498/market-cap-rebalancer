import { useState } from 'react'
import type { Client } from '../api/client'
import type { Limit, Settings, Snapshot } from '../api/schema'
import { SaveNotice, useSave } from '../useSave'

interface Props {
  client: Client
  settings: Settings
  onChange: (next: Snapshot) => void
  onUnauthorized: () => void
}

function validate(raw: string, limit: Limit): string | null {
  const n = Number(raw)
  if (raw.trim() === '' || !Number.isFinite(n)) return '숫자를 입력하세요'
  if (limit.type === 'int' && !Number.isInteger(n)) return '정수만 가능합니다'
  if (n < limit.min || n > limit.max) return `${limit.min} ~ ${limit.max}`
  return null
}

export function StrategyPanel({ client, settings, onChange, onUnauthorized }: Props) {
  const { save, busy, error, saved } = useSave(client, onUnauthorized)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const fields = settings.editable.filter((k) => settings.limits[k])
  const readOnly = Object.keys(settings.config).filter((k) => !settings.editable.includes(k))

  const changes: Record<string, number> = {}
  const problems: Record<string, string> = {}
  for (const key of fields) {
    const draft = drafts[key]
    if (draft === undefined) continue
    const problem = validate(draft, settings.limits[key])
    if (problem) problems[key] = problem
    else if (Number(draft) !== settings.config[key]) changes[key] = Number(draft)
  }
  const changedKeys = Object.keys(changes)
  const canSave = changedKeys.length > 0 && Object.keys(problems).length === 0 && !busy

  async function submit() {
    const next = await save({ config: changes })
    if (next) {
      onChange(next)
      setDrafts({})
    }
  }

  return (
    <section className="card">
      <h2>전략 파라미터</h2>
      <p className="hint">백테스트 결과(results/summary.md)는 기본값 기준입니다. 값을 바꾸면 그 결과와 비교할 수 없습니다.</p>
      <div className="fields">
        {fields.map((key) => {
          const limit = settings.limits[key]
          return (
            <div className="field" key={key}>
              <label htmlFor={`cfg-${key}`}>{key}</label>
              <input
                id={`cfg-${key}`}
                type="number"
                inputMode="decimal"
                min={limit.min}
                max={limit.max}
                step={limit.type === 'int' ? 1 : 'any'}
                value={drafts[key] ?? String(settings.config[key])}
                aria-invalid={key in problems}
                onChange={(e) => setDrafts({ ...drafts, [key]: e.target.value })}
              />
              <span className="hint">{problems[key] ?? `${limit.min} ~ ${limit.max}`}</span>
            </div>
          )
        })}
      </div>

      {changedKeys.length > 0 && (
        <div>
          <h3>바뀐 항목</h3>
          <ul>
            {changedKeys.map((k) => (
              <li key={k}>{k}: {String(settings.config[k])} → {changes[k]}</li>
            ))}
          </ul>
        </div>
      )}
      <button type="button" onClick={() => void submit()} disabled={!canSave}>저장</button>
      <SaveNotice error={error} saved={saved} />

      <details>
        <summary>읽기 전용 항목</summary>
        <dl>
          {readOnly.map((k) => (
            <div key={k} className="row"><dt>{k}</dt><dd>{String(settings.config[k])}</dd></div>
          ))}
        </dl>
      </details>
    </section>
  )
}
