import { useState } from 'react'
import type { Client } from '../api/client'
import type { Limit, Settings, Snapshot } from '../api/types'
import { SaveNotice, useSave } from '../useSave'

interface Props {
  client: Client
  settings: Settings
  onChange: (next: Snapshot) => void
  onUnauthorized: () => void
}

/** 숫자 입력으로 편집하는 항목. 그 밖의 타입은 편집 화면이 생기기 전까지 읽기 전용으로만 보여 준다. */
function isNumeric(limit: Limit | undefined): limit is Limit & { min: number; max: number } {
  return (limit?.type === 'int' || limit?.type === 'float') && limit.min != null && limit.max != null
}

function display(value: Settings['config'][string]): string {
  return Array.isArray(value) ? value.join(', ') : String(value)
}

function validate(raw: string, limit: Limit & { min: number; max: number }): string | null {
  const n = Number(raw)
  if (raw.trim() === '' || !Number.isFinite(n)) return '숫자를 입력하세요'
  if (limit.type === 'int' && !Number.isInteger(n)) return '정수만 가능합니다'
  if (n < limit.min || n > limit.max) return `${limit.min} ~ ${limit.max}`
  return null
}

export function StrategyPanel({ client, settings, onChange, onUnauthorized }: Props) {
  const { save, busy, error, saved } = useSave(client, onUnauthorized)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const fields = settings.editable.filter((k) => isNumeric(settings.limits[k]))
  const readOnly = Object.keys(settings.config).filter((k) => !fields.includes(k))

  const changes: Record<string, number> = {}
  const problems: Record<string, string> = {}
  for (const key of fields) {
    const draft = drafts[key]
    if (draft === undefined) continue
    const limit = settings.limits[key]
    if (!isNumeric(limit)) continue
    const problem = validate(draft, limit)
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
          if (!isNumeric(limit)) return null
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
                value={drafts[key] ?? display(settings.config[key])}
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
              <li key={k}>{k}: {display(settings.config[k])} → {changes[k]}</li>
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
            <div key={k} className="row"><dt>{k}</dt><dd>{display(settings.config[k])}</dd></div>
          ))}
        </dl>
      </details>
    </section>
  )
}
