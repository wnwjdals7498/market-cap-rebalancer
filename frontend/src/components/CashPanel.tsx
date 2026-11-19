import { useEffect, useState } from 'react'
import { UnauthorizedError, type Client } from '../api/client'
import type { CashBalances, CashCommand, MarketCode } from '../api/types'

type Action = CashCommand['action']

function money(value: number, market: MarketCode): string {
  const digits = market === 'KR' ? 0 : 2
  const number = value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
  return `${number}${market === 'KR' ? '원' : '달러'}`
}

export function CashPanel({ client, onUnauthorized }: { client: Client; onUnauthorized: () => void }) {
  const [balances, setBalances] = useState<CashBalances | null>(null)
  const [amounts, setAmounts] = useState<Record<MarketCode, string>>({ KR: '', US: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<CashCommand | null>(null)
  const [typed, setTyped] = useState('')

  useEffect(() => {
    let alive = true
    client.getCash().then((value) => { if (alive) setBalances(value) }).catch((e: unknown) => {
      if (!alive) return
      if (e instanceof UnauthorizedError) onUnauthorized()
      else setError(e instanceof Error ? e.message : String(e))
    })
    return () => { alive = false }
  }, [client, onUnauthorized])

  async function send(command: CashCommand) {
    setBusy(true)
    setError(null)
    try {
      setBalances(await client.postCash(command))
      setAmounts((old) => ({ ...old, [command.market]: '' }))
      setPending(null)
      setTyped('')
    } catch (e) {
      if (e instanceof UnauthorizedError) onUnauthorized()
      else setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function apply(market: MarketCode, action: Action) {
    if (!balances) return
    const raw = amounts[market].trim()
    const amount = Number(raw)
    setError(null)
    if (!raw || !Number.isFinite(amount) || amount < 0 || (action !== 'set' && amount === 0)) {
      setError('올바른 금액을 입력하세요')
      return
    }
    if (action === 'invest' && amount > balances[market].reserve) {
      setError('보관 현금을 초과해 투자금으로 전환할 수 없습니다')
      return
    }
    const command: CashCommand = { market, action, amount }
    if (action === 'reserve') {
      setTyped('')
      setPending(command)
    } else void send(command)
  }

  function confirm() {
    if (!pending) return
    if (typed !== 'CASH') {
      setError('CASH를 정확히 입력하세요')
      return
    }
    void send({ ...pending, confirm: 'CASH' })
  }

  return (
    <section className="card">
      <h2>투자금 / 보관 현금</h2>
      {!balances && !error && <p>불러오는 중…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {balances && (['KR', 'US'] as MarketCode[]).map((market) => (
        <section key={market} className="card" aria-label={`${market} 보관 현금`}>
          <h3>{market === 'KR' ? '한국' : '미국'} ({market})</h3>
          <p>보관 현금 {money(balances[market].reserve, market)} · 투자금 평가액{' '}
            {balances[market].invested_value === null ? '기록 없음' : money(balances[market].invested_value, market)}
            {balances[market].pending && ' · 대기 중'}</p>
          <label htmlFor={`cash-${market}`}>{market} 금액</label>
          <input id={`cash-${market}`} type="number" min="0" step="any" value={amounts[market]}
                 onChange={(e) => setAmounts({ ...amounts, [market]: e.target.value })} />
          <div className="row">
            <button type="button" disabled={busy} onClick={() => apply(market, 'reserve')}>현금 만들기</button>
            <button type="button" disabled={busy} onClick={() => apply(market, 'invest')}>투자금 전환</button>
            <button type="button" disabled={busy} onClick={() => apply(market, 'set')}>직접 설정</button>
          </div>
        </section>
      ))}
      {pending && (
        <div role="dialog" aria-modal="true" aria-label="현금 만들기 확인" className="dialog">
          <p>{pending.market} 보관 현금을 {money(pending.amount, pending.market)} 늘립니다. 계속하려면 <b>CASH</b>를 입력하세요.</p>
          <input type="text" aria-label="확인 문구" autoComplete="off" value={typed}
                 onChange={(e) => setTyped(e.target.value)} />
          <div className="row">
            <button type="button" disabled={busy} onClick={confirm}>확인</button>
            <button type="button" className="secondary" disabled={busy} onClick={() => { setPending(null); setError(null) }}>취소</button>
          </div>
        </div>
      )}
    </section>
  )
}
