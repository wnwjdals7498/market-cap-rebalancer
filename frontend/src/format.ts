const KST = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Seoul',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** ISO 시각 → KST "MM-DD HH:mm". 없으면 "-". */
export function kst(iso: string | null | undefined): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = Object.fromEntries(KST.formatToParts(d).map((x) => [x.type, x.value]))
  return `${p.month}-${p.day} ${p.hour}:${p.minute}`
}

/** 이력의 old/new 는 JSON 문자열이다. 읽을 수 없으면 그대로 보인다. */
export function showJson(raw: string | null): string {
  if (raw === null) return '-'
  try {
    return String(JSON.parse(raw))
  } catch {
    return raw
  }
}
