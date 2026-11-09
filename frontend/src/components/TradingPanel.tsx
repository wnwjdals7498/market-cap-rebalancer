import { useState } from 'react'
import type { Client } from '../api/client'
import type { MarketCode, Settings, SettingsPatch, Snapshot } from '../api/types'
import { SaveNotice, useSave } from '../useSave'

interface Props {
  client: Client
  settings: Settings
  onChange: (next: Snapshot) => void
  onUnauthorized: () => void
}

const CONFIRM_WORD = 'SUBMIT'

export function TradingPanel({ client, settings, onChange, onUnauthorized }: Props) {
  const { save, busy, error, saved } = useSave(client, onUnauthorized)
  // 확인이 필요한 변경(주문 전송 켜기, 전송 중 broker 를 kis 로 바꾸기)은 SUBMIT 입력 뒤에 보낸다.
  const [pending, setPending] = useState<SettingsPatch | null>(null)
  const [typed, setTyped] = useState('')
  const [typedError, setTypedError] = useState<string | null>(null)
  const { trading, paused } = settings

  async function apply(body: SettingsPatch) {
    const next = await save(body)
    if (next) onChange(next)
    return next !== null
  }

  function toggleSubmit() {
    if (trading.submit) void apply({ trading: { submit: false } }) // 끌 때는 확인이 필요 없다
    else askConfirm({ trading: { submit: true } })
  }

  function changeBroker(broker: 'sim' | 'kis') {
    // 이미 전송 중일 때 kis 로 바꾸면 실제 주문이 나가므로 켤 때와 같은 확인을 거친다. select 는 제어 값이라 취소하면 원래대로 돌아간다.
    if (broker === 'kis' && trading.submit) askConfirm({ trading: { broker } })
    else void apply({ trading: { broker } })
  }

  function askConfirm(body: SettingsPatch) {
    setTyped('')
    setTypedError(null)
    setPending(body)
  }

  async function confirmSubmit() {
    if (typed !== CONFIRM_WORD) {
      setTypedError(`${CONFIRM_WORD} 를 정확히 입력해야 합니다.`)
      return
    }
    if (pending && (await apply({ ...pending, confirm: CONFIRM_WORD }))) setPending(null)
  }

  return (
    <section className="card">
      <h2>운용</h2>
      <div className="row">
        <label htmlFor="broker">broker</label>
        <select
          id="broker"
          value={trading.broker}
          disabled={busy}
          onChange={(e) => changeBroker(e.target.value as 'sim' | 'kis')}
        >
          <option value="sim">sim</option>
          <option value="kis">kis</option>
        </select>
      </div>
      <div className="row">
        <input id="submit" type="checkbox" checked={trading.submit} disabled={busy} onChange={toggleSubmit} />
        <label htmlFor="submit">주문 전송 (submit)</label>
      </div>
      <div className="row">
        <input id="allow_real" type="checkbox" checked={trading.allow_real} disabled readOnly />
        <label htmlFor="allow_real">allow_real</label>
        <span className="hint">CLI 전용 — jobs.py config --allow-real</span>
      </div>
      {(['KR', 'US'] as MarketCode[]).map((code) => (
        <div className="row" key={code}>
          <input
            id={`paused-${code}`}
            type="checkbox"
            checked={paused[code]}
            disabled={busy}
            onChange={(e) => void apply({ paused: { [code]: e.target.checked } })}
          />
          <label htmlFor={`paused-${code}`}>{code} 일시정지</label>
        </div>
      ))}
      <SaveNotice error={error} saved={saved} />

      {pending && (
        <div role="dialog" aria-modal="true" aria-label="주문 전송 켜기" className="dialog">
          <p>주문 전송을 켜면 다음 tick 부터 broker({pending.trading?.broker ?? trading.broker}) 로 주문이 나갑니다. 계속하려면 <b>{CONFIRM_WORD}</b> 를 입력하세요.</p>
          <input
            type="text"
            aria-label="확인 문구"
            autoComplete="off"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
          />
          {typedError && <p role="alert" className="error">{typedError}</p>}
          <div className="row">
            <button type="button" onClick={() => void confirmSubmit()} disabled={busy}>켜기</button>
            <button type="button" className="secondary" onClick={() => setPending(null)}>취소</button>
          </div>
        </div>
      )}
    </section>
  )
}
