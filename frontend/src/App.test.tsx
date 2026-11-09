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
    const input = await screen.findByLabelText('stock_pct')
    await userEvent.clear(input)
    await userEvent.type(input, '70')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(patchCalls(fetchFn)).toHaveLength(1))
    expect(JSON.parse(String(patchCalls(fetchFn)[0][1]?.body))).toEqual({ config: { stock_pct: 70 } })
    expect(await screen.findByRole('alert')).toHaveTextContent('stock_pct 는 0~100 사이여야 합니다')
  })

  it('renders KST times for each market', async () => {
    stubFetch({ 'GET /api/v1/status': () => json(status) })
    render(<StatusPanel client={createClient('t')} onUnauthorized={() => {}} />)
    expect(await screen.findByText('11-10 01:45')).toBeInTheDocument() // 16:45 UTC is next day 01:45 KST
    expect(screen.getByText('11-10 14:50')).toBeInTheDocument()
  })
})
