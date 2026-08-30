/** Парсит ISO-строку от бэкенда (наивный UTC без Z) как UTC и форматирует в локальной таймзоне браузера. */
export function formatBrowserDate(iso: string | null | undefined, opts?: Intl.DateTimeFormatOptions): string {
  if (!iso) return "-"
  // Бэкенд отдаёт isoformat() без таймзоны (наивный UTC). Если уже есть Z или офсет — не трогаем.
  const hasTz = iso.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(iso)
  const normalized = hasTz ? iso : iso + "Z"
  const d = new Date(normalized)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString("ru-RU", opts)
}

export function formatBrowserDateOnly(iso: string | null | undefined): string {
  return formatBrowserDate(iso, { day: "2-digit", month: "2-digit", year: "numeric" })
}
