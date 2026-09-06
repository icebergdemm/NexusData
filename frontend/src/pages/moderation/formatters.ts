import { formatBrowserDate } from "../../lib/format-date"

const JOB_COLORS: Record<string, string> = {
  captain: "#A6BAD6",
  engineer: "#E0B86E",
  mechanic: "#80DBE6",
  securityofficer: "#917A6E",
  medicaldoctor: "#CF4F30",
}

export function getJobColor(jobId: string | null): string | undefined {
  return jobId ? JOB_COLORS[jobId.toLowerCase()] : undefined
}

function parseSessionTime(iso: string | null | undefined): number | null {
  if (!iso) return null
  const hasTimezone = iso.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(iso)
  const normalized = hasTimezone ? iso : iso.replace(" ", "T") + "+03:00"
  const timestamp = Date.parse(normalized)
  return Number.isNaN(timestamp) ? null : timestamp
}

export function formatSessionDuration(
  joinTime: string | null | undefined,
  now = Date.now(),
): string {
  const joinedAt = parseSessionTime(joinTime)
  if (joinedAt == null) return "-"
  const minutes = Math.max(0, Math.floor((now - joinedAt) / 60_000))
  return `${minutes} мин`
}

export function formatSessionStart(joinTime: string | null | undefined): string {
  if (parseSessionTime(joinTime) == null) return "-"
  return `с ${formatBrowserDate(joinTime, { hour: "2-digit", minute: "2-digit" })}`
}
