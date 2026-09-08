/** Парсит ISO-строку от бэкенда (наивный MSK без таймзоны) и форматирует в локальной таймзоне браузера.
 *  Бэкенд хранит даты как MSK naive ( _now_msk().isoformat() ), поэтому без суффикса считаем +03:00 (Europe/Moscow).
 *  Если уже есть Z или офсет — парсим как есть. */
export function formatBrowserDate(iso: string | null | undefined, opts?: Intl.DateTimeFormatOptions): string {
  if (!iso) return "-"
  const hasTz = iso.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(iso)
  let normalized = hasTz ? iso : iso.replace(" ", "T") + "+03:00"
  // Нормализация для строк вида "2024-01-01 12:00:00.123456+03:00" — оставляем как есть, Date справится
  const d = new Date(normalized)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString("ru-RU", opts)
}

export function formatBrowserDateOnly(iso: string | null | undefined): string {
  return formatBrowserDate(iso, { day: "2-digit", month: "2-digit", year: "numeric" })
}

/**
 * Rating API dates are UTC: event timestamps carry an offset, while older
 * SQLAlchemy datetime fields can arrive as naive UTC. Keep the older generic
 * helper's MSK compatibility behavior intact and make the rating page
 * unambiguously browser-local.
 */
export function formatRatingDate(iso: string | null | undefined, opts?: Intl.DateTimeFormatOptions): string {
  if (!iso) return "-"
  const hasTz = iso.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(iso)
  const normalized = hasTz ? iso : iso.replace(" ", "T") + "Z"
  const d = new Date(normalized)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString("ru-RU", opts)
}
