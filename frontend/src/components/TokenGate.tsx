import { useState, type FormEvent } from 'react'

export function TokenGate({ onSubmit }: { onSubmit: (token: string) => void }) {
  const [value, setValue] = useState('')

  function submit(e: FormEvent) {
    e.preventDefault()
    if (value.trim()) onSubmit(value.trim())
  }

  return (
    <form className="card gate" onSubmit={submit}>
      <h1>MCR 설정</h1>
      <p className="hint">서버 .env 의 MCR_API_TOKEN 값을 입력하세요. 이 탭에만 보관되고 탭을 닫으면 지워집니다.</p>
      <label htmlFor="token">토큰</label>
      <input
        id="token"
        type="password"
        autoComplete="off"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <button type="submit">확인</button>
    </form>
  )
}
