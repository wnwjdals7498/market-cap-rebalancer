import { useState } from 'react'
import { UnauthorizedError, type Client } from './api/client'
import type { SettingsPatch, Snapshot } from './api/types'

/** PATCH 한 번의 진행·결과 상태. 401 은 onUnauthorized 로, 그 밖의 오류는 detail 문구로 보인다. */
export function useSave(client: Client, onUnauthorized: () => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  async function save(body: SettingsPatch): Promise<Snapshot | null> {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      const next = await client.patchSettings(body)
      setSaved(true)
      return next
    } catch (e) {
      if (e instanceof UnauthorizedError) onUnauthorized()
      else setError(e instanceof Error ? e.message : String(e))
      return null
    } finally {
      setBusy(false)
    }
  }

  return { save, busy, error, saved }
}

export function SaveNotice({ error, saved }: { error: string | null; saved: boolean }) {
  if (error) return <p role="alert" className="error">{error}</p>
  if (saved) return <p role="status" className="ok">저장했습니다. 다음 tick(최대 5분 뒤)부터 적용됩니다.</p>
  return null
}
