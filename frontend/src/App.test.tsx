import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from './App'
import { StatusPanel } from './components/StatusPanel'
import { createClient } from './api/client'
import { defaultRoutes, json, patchCalls, settings, status, stubFetch } from './test/helpers'

describe('settings UI', () => {
  it('asks for a token and keeps it only in sessionStorage', async () => {
    stubFetch(defaultRoutes())
    render(<App />)
    await userEvent.type(await screen.findByLabelText('토큰'), 'abc-123')
    await userEvent.click(screen.getByRole('button', { name: '확인' }))
    await waitFor(() => expect(sessionStorage.getItem('mcr_token')).toBe('abc-123'))
    expect(localStorage.length).toBe(0)
  })

  it('returns to the token gate on 401', async () => {
    sessionStorage.setItem('mcr_token', 'stale')
    stubFetch({ 'GET /api/v1/settings': () => json({ detail: 'unauthorized' }, 401) })
    render(<App />)
    expect(await screen.findByLabelText('토큰')).toBeInTheDocument()
    expect(sessionStorage.getItem('mcr_token')).toBeNull()
  })

  it('requires typing SUBMIT before enabling order submission', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const fetchFn = stubFetch({
      ...defaultRoutes(),
      'PATCH /api/v1/settings': () =>
        json({ ...settings, trading: { ...settings.trading, submit: true } }),
    })
    render(<App />)
    await userEvent.click(await screen.findByRole('checkbox', { name: /주문 전송/ }))
    const dialog = await screen.findByRole('dialog')
    const box = within(dialog).getByRole('textbox')
    await userEvent.type(box, 'submit')
    await userEvent.click(within(dialog).getByRole('button', { name: '켜기' }))
    expect(patchCalls(fetchFn)).toHaveLength(0)

    await userEvent.clear(box)
    await userEvent.type(box, 'SUBMIT')
    await userEvent.click(within(dialog).getByRole('button', { name: '켜기' }))
    await waitFor(() => expect(patchCalls(fetchFn)).toHaveLength(1))
    expect(JSON.parse(String(patchCalls(fetchFn)[0][1]?.body))).toEqual({
      trading: { submit: true },
      confirm: 'SUBMIT',
    })
  })

  it('requires typing SUBMIT before switching to kis while submission is on', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const live = { ...settings, trading: { ...settings.trading, submit: true } }
    const fetchFn = stubFetch({
      ...defaultRoutes(),
      'GET /api/v1/settings': () => json(live),
      'PATCH /api/v1/settings': () => json({ ...live, trading: { ...live.trading, broker: 'kis' } }),
    })
    render(<App />)
    const select = await screen.findByLabelText('broker')
    await userEvent.selectOptions(select, 'kis')
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('broker(kis)')
    const box = within(dialog).getByRole('textbox')
    await userEvent.type(box, 'submit')
    await userEvent.click(within(dialog).getByRole('button', { name: '켜기' }))
    expect(patchCalls(fetchFn)).toHaveLength(0)

    await userEvent.click(within(dialog).getByRole('button', { name: '취소' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(select).toHaveValue('sim') // cancelled: back to the original value
    expect(patchCalls(fetchFn)).toHaveLength(0)

    await userEvent.selectOptions(select, 'kis')
    const again = await screen.findByRole('dialog')
    await userEvent.type(within(again).getByRole('textbox'), 'SUBMIT')
    await userEvent.click(within(again).getByRole('button', { name: '켜기' }))
    await waitFor(() => expect(patchCalls(fetchFn)).toHaveLength(1))
    expect(JSON.parse(String(patchCalls(fetchFn)[0][1]?.body))).toEqual({
      trading: { broker: 'kis' },
      confirm: 'SUBMIT',
    })
  })

  it('shows allow_real as read-only', async () => {
    sessionStorage.setItem('mcr_token', 't')
    stubFetch(defaultRoutes())
    render(<App />)
    expect(await screen.findByRole('checkbox', { name: /allow_real/ })).toBeDisabled()
    expect(screen.getByText(/CLI 전용/)).toBeInTheDocument()
  })

  it('sends only changed strategy fields and shows the server detail on 400', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const fetchFn = stubFetch({
      ...defaultRoutes(),
      'PATCH /api/v1/settings': () => json({ detail: 'stock_pct 는 0~100 사이여야 합니다' }, 400),
    })
    render(<App />)
    const input = await screen.findByLabelText('주식 비중 (%)')
    await userEvent.clear(input)
    await userEvent.type(input, '70')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(patchCalls(fetchFn)).toHaveLength(1))
    expect(JSON.parse(String(patchCalls(fetchFn)[0][1]?.body))).toEqual({ config: { stock_pct: 70 } })
    expect(await screen.findByRole('alert')).toHaveTextContent('stock_pct 는 0~100 사이여야 합니다')
  })

  it('weights editor blocks save until sum is 100', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const fetchFn = stubFetch({ ...defaultRoutes(), 'PATCH /api/v1/settings': () => json(settings) })
    render(<App />)
    const first = await screen.findByLabelText('1위 비율')
    const save = screen.getByRole('button', { name: '저장' })
    await userEvent.clear(first)
    await userEvent.type(first, '40')
    expect(screen.getByText(/합계가 100이어야 합니다 \(현재 90\)/)).toBeInTheDocument()
    expect(save).toBeDisabled()

    await userEvent.clear(screen.getByLabelText('4위 비율'))
    await userEvent.type(screen.getByLabelText('4위 비율'), '10')
    expect(save).toBeEnabled()
    await userEvent.click(save)
    await waitFor(() => expect(patchCalls(fetchFn)).toHaveLength(1))
    expect(JSON.parse(String(patchCalls(fetchFn)[0][1]?.body))).toEqual({ config: { rank_weights: [40, 30, 20, 10, 0] } })
  })

  it('safe weights edits three slots summing to 100', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const fetchFn = stubFetch({ ...defaultRoutes(), 'PATCH /api/v1/settings': () => json(settings) })
    render(<App />)
    const group = await screen.findByRole('group', { name: '안전자산 비율 (단기채/장기채/금, 합 100)' })
    expect(within(group).getAllByRole('spinbutton')).toHaveLength(3)
    const short = within(group).getByLabelText('단기채 비율')
    const gold = within(group).getByLabelText('금 비율')
    await userEvent.clear(short)
    await userEvent.type(short, '80')
    await userEvent.clear(gold)
    await userEvent.type(gold, '20')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(patchCalls(fetchFn)).toHaveLength(1))
    expect(JSON.parse(String(patchCalls(fetchFn)[0][1]?.body))).toEqual({ config: { safe_weights: [80, 0, 20] } })
  })

  it('risk weights rejects sum not 100', async () => {
    sessionStorage.setItem('mcr_token', 't')
    stubFetch(defaultRoutes())
    render(<App />)
    const group = await screen.findByRole('group', { name: '미국 위험자산 비율 (개별주/나스닥/S&P500, 합 100)' })
    expect(within(group).getAllByRole('spinbutton')).toHaveLength(3)
    expect(within(group).getByLabelText('나스닥 비율')).toBeInTheDocument()
    expect(within(group).getByLabelText('S&P500 비율')).toBeInTheDocument()
    const krGroup = screen.getByRole('group', { name: '한국 위험자산 비율 (개별주/코스피/코스닥, 합 100)' })
    expect(within(krGroup).getByLabelText('코스피 비율')).toBeInTheDocument()
    expect(within(krGroup).getByLabelText('코스닥 비율')).toBeInTheDocument()
    const first = within(group).getByLabelText('개별주 비율')
    await userEvent.clear(first)
    await userEvent.type(first, '60')
    expect(screen.getByText(/합계가 100이어야 합니다 \(현재 60\)/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '저장' })).toBeDisabled()
  })

  it('uses value length when weight slots are missing', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const safe = { ...settings.limits.safe_weights, slots: null }
    stubFetch({ ...defaultRoutes(), 'GET /api/v1/settings': () => json({
      ...settings, limits: { ...settings.limits, safe_weights: safe },
    }) })
    render(<App />)
    const group = await screen.findByRole('group', { name: '안전자산 비율 (단기채/장기채/금, 합 100)' })
    expect(within(group).getAllByRole('spinbutton')).toHaveLength(3)
    expect(within(group).getByLabelText('장기채 비율')).toBeInTheDocument()
  })

  it('uses the configured slot count for the editor', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const safe = { ...settings.limits.safe_weights, slots: 2 }
    stubFetch({ ...defaultRoutes(), 'GET /api/v1/settings': () => json({
      ...settings, limits: { ...settings.limits, safe_weights: safe },
    }) })
    render(<App />)
    const group = await screen.findByRole('group', { name: '안전자산 비율 (단기채/장기채/금, 합 100)' })
    expect(within(group).getAllByRole('spinbutton')).toHaveLength(2)
  })

  it('shows all six ETF ticker editors', async () => {
    sessionStorage.setItem('mcr_token', 't')
    stubFetch(defaultRoutes())
    render(<App />)
    expect(await screen.findByLabelText('미국 금 ETF')).toHaveValue('GLD')
    expect(screen.getByLabelText('한국 금 ETF')).toHaveValue('411060.KS')
    expect(screen.getByLabelText('미국 나스닥 ETF')).toHaveValue('QQQ')
    expect(screen.getByLabelText('미국 S&P500 ETF')).toHaveValue('SPY')
    expect(screen.getByLabelText('한국 코스피 ETF')).toHaveValue('069500.KS')
    expect(screen.getByLabelText('한국 코스닥 ETF')).toHaveValue('229200.KS')
  })

  it('trade_every select sends week', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const fetchFn = stubFetch({ ...defaultRoutes(), 'PATCH /api/v1/settings': () => json(settings) })
    render(<App />)
    const select = await screen.findByLabelText('매매 주기')
    expect(select).toHaveValue('day')
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual(['일간', '주간', '월간'])
    await userEvent.selectOptions(select, '주간')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(patchCalls(fetchFn)).toHaveLength(1))
    expect(JSON.parse(String(patchCalls(fetchFn)[0][1]?.body))).toEqual({ config: { trade_every: 'week' } })
  })

  it('edits tickers and booleans and sends only the changed keys', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const fetchFn = stubFetch({ ...defaultRoutes(), 'PATCH /api/v1/settings': () => json(settings) })
    render(<App />)
    await userEvent.click(await screen.findByLabelText('환전 안내 사용'))
    const bond = screen.getByLabelText('미국 단기채')
    await userEvent.clear(bond)
    await userEvent.type(bond, 'bil')
    const pool = screen.getByLabelText('미국 후보 풀 (자동 갱신)')
    await userEvent.clear(pool)
    await userEvent.type(pool, 'aapl, nvda, msft, googl, meta')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(patchCalls(fetchFn)).toHaveLength(1))
    expect(JSON.parse(String(patchCalls(fetchFn)[0][1]?.body))).toEqual({
      config: {
        fx_enabled: false,
        us_short_bond: 'BIL',
        us_universe: ['AAPL', 'NVDA', 'MSFT', 'GOOGL', 'META'],
      },
    })
  })

  it('pool list accepts 15 comma separated tickers', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const fetchFn = stubFetch({ ...defaultRoutes(), 'PATCH /api/v1/settings': () => json(settings) })
    render(<App />)
    const pool = await screen.findByLabelText('미국 후보 풀 (자동 갱신)')
    expect(screen.getAllByText('5개 (5 ~ 30), 쉼표로 구분')).toHaveLength(2) // US and KR pools
    const fifteen = 'aapl,nvda,msft,googl,amzn,meta,tsla,avgo,tsm,brk-b,lly,wmt,jpm,v,orcl'
    await userEvent.clear(pool)
    await userEvent.type(pool, fifteen)
    expect(screen.getByText('15개 (5 ~ 30), 쉼표로 구분')).toBeInTheDocument()
    const save = screen.getByRole('button', { name: '저장' })
    expect(save).toBeEnabled()
    await userEvent.click(save)
    await waitFor(() => expect(patchCalls(fetchFn)).toHaveLength(1))
    expect(JSON.parse(String(patchCalls(fetchFn)[0][1]?.body))).toEqual({
      config: { us_universe: fifteen.toUpperCase().split(',') },
    })
  })

  it('pool list rejects 4', async () => {
    sessionStorage.setItem('mcr_token', 't')
    stubFetch({ ...defaultRoutes(), 'PATCH /api/v1/settings': () => json(settings) })
    render(<App />)
    const pool = await screen.findByLabelText('미국 후보 풀 (자동 갱신)')
    await userEvent.clear(pool)
    await userEvent.type(pool, 'AAPL, NVDA, MSFT, GOOGL')
    expect(screen.getByText('종목 5 ~ 30개여야 합니다 (현재 4)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '저장' })).toBeDisabled()
  })

  it('pool list rejects duplicates', async () => {
    sessionStorage.setItem('mcr_token', 't')
    stubFetch({ ...defaultRoutes(), 'PATCH /api/v1/settings': () => json(settings) })
    render(<App />)
    const pool = await screen.findByLabelText('미국 후보 풀 (자동 갱신)')
    await userEvent.clear(pool)
    await userEvent.type(pool, 'aapl, AAPL, msft, googl, meta')
    expect(screen.getByText('중복된 종목이 있습니다')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '저장' })).toBeDisabled()
  })

  it('shows strategy state and the status fx plan', async () => {
    stubFetch({
      'GET /api/v1/status': () => json({
        ...status,
        fx: { from_market: 'US', amount: 9000, currency: 'USD', created: '2026-11-10', ready: null },
      }),
    })
    render(<StatusPanel client={createClient('t')} onUnauthorized={() => {}} />)
    expect(await screen.findAllByText('AAPL > NVDA > MSFT', { selector: 'td' })).toHaveLength(2) // ranking and confirmed
    expect(screen.getByText('SHY 40.0%, AAPL 30.0%')).toBeInTheDocument()
    expect(screen.getByText(/미국 포켓 \$9,000 환전 필요 \(생성 2026-11-10\), 현금 확보 중/)).toBeInTheDocument()
  })

  it('shows the cash to exchange once the fx plan is ready', async () => {
    stubFetch({
      'GET /api/v1/status': () => json({
        ...status,
        fx: { from_market: 'US', amount: 9000, currency: 'USD', created: '2026-11-10', ready: '2026-11-11', exchange: 5200.5 },
      }),
    })
    render(<StatusPanel client={createClient('t')} onUnauthorized={() => {}} />)
    expect(await screen.findByText(/미국 포켓 \$5,200.5 환전 필요 \(생성 2026-11-10\), 현금 확보 완료 2026-11-11/)).toBeInTheDocument()
  })

  it('status shows fx plan as none when there is no plan', async () => {
    stubFetch({ 'GET /api/v1/status': () => json(status) })
    render(<StatusPanel client={createClient('t')} onUnauthorized={() => {}} />)
    expect(await screen.findByText('없음', { selector: 'p' })).toBeInTheDocument()
  })

  it('renders KST times for each market', async () => {
    stubFetch({ 'GET /api/v1/status': () => json(status) })
    render(<StatusPanel client={createClient('t')} onUnauthorized={() => {}} />)
    expect(await screen.findByText('11-10 01:45')).toBeInTheDocument() // 16:45 UTC is next day 01:45 KST
    expect(screen.getByText('11-10 14:50')).toBeInTheDocument()
  })
})

describe('stock names', () => {
  it('shows stock names next to tickers', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const names = { AAPL: '애플(AAPL)', NVDA: '엔비디아(NVDA)', MSFT: '마이크로소프트(MSFT)',
      SHY: '미국 단기국채(SHY)', '229200.KS': 'KODEX 코스닥150' }
    stubFetch({ ...defaultRoutes(),
      'GET /api/v1/settings': () => json({ ...settings, names }),
      'GET /api/v1/status': () => json({ ...status, names }),
    })
    render(<App />)
    expect(await screen.findAllByText('애플(AAPL) > 엔비디아(NVDA) > 마이크로소프트(MSFT)')).toHaveLength(2)
    expect(screen.getByText(/미국 단기국채\(SHY\) 40.0%/)).toBeInTheDocument()
    expect(screen.getByText(/KODEX 코스닥150/)).toBeInTheDocument()
    expect(screen.getByLabelText('한국 코스닥 ETF')).toHaveValue('229200.KS')
  })
})

describe('cash reserve', () => {
  const cash = {
    KR: { reserve: 1000, pending: false, principal: 10000, invested_value: 10000, currency: 'KRW' },
    US: { reserve: 50, pending: true, principal: 1000, invested_value: 900, currency: 'USD' },
  }

  it('cash reserve requires CASH confirmation', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const fetchFn = stubFetch({ ...defaultRoutes(), 'GET /api/v1/cash': () => json(cash),
      'POST /api/v1/cash': () => json(cash) })
    render(<App />)
    await userEvent.type(await screen.findByLabelText('KR 금액'), '500')
    expect(screen.getByRole('region', { name: 'KR 보관 현금' })).toHaveTextContent('보관 현금 1,000원 · 투자금 평가액 10,000원')
    expect(screen.getByRole('region', { name: 'US 보관 현금' })).toHaveTextContent('보관 현금 50.00달러 · 투자금 평가액 900.00달러 · 대기 중')
    await userEvent.click(within(screen.getByRole('region', { name: 'KR 보관 현금' })).getByRole('button', { name: '현금 만들기' }))
    const dialog = screen.getByRole('dialog', { name: '현금 만들기 확인' })
    await userEvent.type(within(dialog).getByLabelText('확인 문구'), 'cash')
    await userEvent.click(within(dialog).getByRole('button', { name: '확인' }))
    expect(fetchFn.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
    await userEvent.clear(within(dialog).getByLabelText('확인 문구'))
    await userEvent.type(within(dialog).getByLabelText('확인 문구'), 'CASH')
    await userEvent.click(within(dialog).getByRole('button', { name: '확인' }))
    await waitFor(() => expect(fetchFn.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1))
    const call = fetchFn.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ market: 'KR', action: 'reserve', amount: 500, confirm: 'CASH' })
  })

  it('invest rejects more than reserve', async () => {
    sessionStorage.setItem('mcr_token', 't')
    const fetchFn = stubFetch({ ...defaultRoutes(), 'GET /api/v1/cash': () => json(cash) })
    render(<App />)
    await userEvent.type(await screen.findByLabelText('US 금액'), '51')
    await userEvent.click(within(screen.getByRole('region', { name: 'US 보관 현금' })).getByRole('button', { name: '투자금 전환' }))
    expect(screen.getByText(/보관 현금.*초과/)).toBeInTheDocument()
    expect(fetchFn.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })
})
