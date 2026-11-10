import { useState } from 'react'
import type { Client } from '../api/client'
import type { ConfigValue, Limit, Settings, Snapshot } from '../api/types'
import { SaveNotice, useSave } from '../useSave'

interface Props {
  client: Client
  settings: Settings
  onChange: (next: Snapshot) => void
  onUnauthorized: () => void
}

/** 입력 중인 값. 숫자·문자열·종목 목록(쉼표 구분)은 문자열 그대로, 체크박스는 boolean, 비율은 칸마다 문자열. */
type Draft = string | boolean | string[]

const EVERY_LABEL: Record<string, string> = { day: '일간', week: '주간', month: '월간' }

function display(value: ConfigValue | Draft | undefined): string {
  return Array.isArray(value) ? value.join(', ') : String(value)
}

/** 쉼표로 구분한 입력 → 대문자 종목 목록 (빈 항목은 그대로 둬 검사에서 걸린다) */
function splitTickers(raw: string): string[] {
  return raw.trim() === '' ? [] : raw.split(',').map((t) => t.trim().toUpperCase())
}

/** 서버 값 → 입력 칸 초기값 */
function initial(value: ConfigValue, limit?: Limit): Draft {
  if (Array.isArray(value)) return limit?.type === 'tickers' ? value.join(', ') : value.map(String)
  if (typeof value === 'boolean') return value
  return String(value)
}

function sum(values: string[]): number {
  return values.reduce((total, v) => total + (Number(v) || 0), 0)
}

function same(a: ConfigValue, b: ConfigValue): boolean {
  return Array.isArray(a) && Array.isArray(b) ? a.length === b.length && a.every((v, i) => v === b[i]) : a === b
}

function range(limit: Limit): string {
  return `${limit.min ?? ''} ~ ${limit.max ?? ''}`
}

/** 입력값 하나를 검사해 서버로 보낼 값을 만든다. 서버도 다시 검증한다(여기서는 저장 전 안내만). */
function parse(draft: Draft, limit: Limit): { value: ConfigValue } | { problem: string } {
  switch (limit.type) {
    case 'int':
    case 'float': {
      const raw = String(draft)
      const n = Number(raw)
      if (raw.trim() === '' || !Number.isFinite(n)) return { problem: '숫자를 입력하세요' }
      if (limit.type === 'int' && !Number.isInteger(n)) return { problem: '정수만 가능합니다' }
      if (n < (limit.min ?? -Infinity) || n > (limit.max ?? Infinity)) return { problem: range(limit) }
      return { value: n }
    }
    case 'bool':
      return { value: draft === true }
    case 'choice':
      return limit.options?.includes(String(draft)) ? { value: String(draft) } : { problem: '선택하세요' }
    case 'weights': {
      const cells = draft as string[]
      const numbers = cells.map((c) => Number(c))
      if (cells.some((c) => c.trim() === '') || numbers.some((n) => !Number.isFinite(n))) {
        return { problem: '숫자를 입력하세요' }
      }
      if (numbers.some((n) => n < (limit.min ?? 0) || n > (limit.max ?? 100))) return { problem: range(limit) }
      const total = sum(cells)
      if (Math.abs(total - 100) > 1e-6) return { problem: `합계가 100이어야 합니다 (현재 ${total})` }
      return { value: numbers }
    }
    case 'tickers': {
      const names = splitTickers(String(draft))
      const [lo, hi] = [limit.min ?? 1, limit.max ?? Infinity]
      if (names.some((t) => t === '')) return { problem: '빈 종목이 있습니다 (쉼표 확인)' }
      if (names.length < lo || names.length > hi) return { problem: `종목 ${range(limit)}개여야 합니다 (현재 ${names.length})` }
      if (new Set(names).size !== names.length) return { problem: '중복된 종목이 있습니다' }
      return { value: names }
    }
    case 'ticker': {
      const name = String(draft).trim().toUpperCase()
      return name === '' ? { problem: '종목 코드를 입력하세요' } : { value: name }
    }
  }
}

export function StrategyPanel({ client, settings, onChange, onUnauthorized }: Props) {
  const { save, busy, error, saved } = useSave(client, onUnauthorized)
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const fields = settings.editable.filter((k) => settings.limits[k] && k in settings.config)
  const readOnly = Object.keys(settings.config).filter((k) => !fields.includes(k))

  const changes: Record<string, ConfigValue> = {}
  const problems: Record<string, string> = {}
  for (const key of fields) {
    if (drafts[key] === undefined) continue
    const result = parse(drafts[key], settings.limits[key])
    if ('problem' in result) problems[key] = result.problem
    else if (!same(result.value, settings.config[key])) changes[key] = result.value
  }
  const changedKeys = Object.keys(changes)
  const canSave = changedKeys.length > 0 && Object.keys(problems).length === 0 && !busy

  function edit(key: string, draft: Draft) {
    setDrafts({ ...drafts, [key]: draft })
  }

  function cells(key: string): string[] {
    const draft = drafts[key] ?? initial(settings.config[key], settings.limits[key])
    return Array.isArray(draft) ? draft : []
  }

  function editCell(key: string, index: number, value: string) {
    const next = cells(key).slice()
    next[index] = value
    edit(key, next)
  }

  async function submit() {
    const next = await save({ config: changes })
    if (next) {
      onChange(next)
      setDrafts({})
    }
  }

  function input(key: string, limit: Limit) {
    const value = drafts[key] ?? initial(settings.config[key], limit)
    const id = `cfg-${key}`
    switch (limit.type) {
      case 'int':
      case 'float':
        return (
          <input
            id={id}
            type="number"
            inputMode="decimal"
            min={limit.min ?? undefined}
            max={limit.max ?? undefined}
            step={limit.type === 'int' ? 1 : 'any'}
            value={String(value)}
            aria-invalid={key in problems}
            onChange={(e) => edit(key, e.target.value)}
          />
        )
      case 'bool':
        return <input id={id} type="checkbox" checked={value === true} onChange={(e) => edit(key, e.target.checked)} />
      case 'choice':
        return (
          <select id={id} value={String(value)} onChange={(e) => edit(key, e.target.value)}>
            {(limit.options ?? []).map((o) => (
              <option key={o} value={o}>{key === 'trade_every' ? (EVERY_LABEL[o] ?? o) : o}</option>
            ))}
          </select>
        )
      case 'ticker':
        return (
          <input id={id} type="text" value={String(value)} aria-invalid={key in problems}
                 onChange={(e) => edit(key, e.target.value)} />
        )
      case 'tickers':
        return (
          <input id={id} type="text" value={String(value)} aria-invalid={key in problems}
                 spellCheck={false} autoCapitalize="characters"
                 onChange={(e) => edit(key, e.target.value)} />
        )
      case 'weights':
        return (
          <div className="cells" role="group" aria-labelledby={`${id}-label`}>
            {cells(key).map((cell, i) => (
              <input
                key={i}
                type="number"
                inputMode="decimal"
                step="any"
                aria-label={`${i + 1}위 비율`}
                value={cell}
                aria-invalid={key in problems}
                onChange={(e) => editCell(key, i, e.target.value)}
              />
            ))}
          </div>
        )
    }
  }

  function hint(key: string, limit: Limit) {
    if (problems[key]) return problems[key]
    if (limit.type === 'int' || limit.type === 'float') return range(limit)
    if (limit.type === 'weights') return `합계 ${sum(cells(key))} / 100`
    if (limit.type === 'tickers') {
      return `${splitTickers(String(drafts[key] ?? initial(settings.config[key], limit))).length}개 (${range(limit)}), 쉼표로 구분`
    }
    return ''
  }

  return (
    <section className="card">
      <h2>전략 파라미터</h2>
      <p className="hint">백테스트 결과(results/summary.md)는 기본값 기준입니다. 값을 바꾸면 그 결과와 비교할 수 없습니다.</p>
      <div className="fields">
        {fields.map((key) => {
          const limit = settings.limits[key]
          const wide = limit.type === 'weights' || limit.type === 'tickers'
          return (
            <div className={wide ? 'field wide' : 'field'} key={key}>
              <label id={`cfg-${key}-label`} htmlFor={`cfg-${key}`}>{limit.label}</label>
              {input(key, limit)}
              <span className="hint">{hint(key, limit)}</span>
            </div>
          )
        })}
      </div>

      {changedKeys.length > 0 && (
        <div>
          <h3>바뀐 항목</h3>
          <ul>
            {changedKeys.map((k) => (
              <li key={k}>{settings.limits[k].label}: {display(settings.config[k])} → {display(changes[k])}</li>
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
