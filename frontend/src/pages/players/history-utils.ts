import type { IpRow, KillRow, NameRow, RoleActionRow, SessionRow } from "./detail-types"

const THIRTY_DAYS_MS = 30 * 24 * 60 * 60 * 1000

function parseTimestamp(value: string | null): number | null {
  if (!value) return null
  const normalized = value.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(value)
    ? value
    : `${value.replace(" ", "T")}+03:00`
  const timestamp = Date.parse(normalized)
  return Number.isNaN(timestamp) ? null : timestamp
}

function durationForSummary(session: SessionRow, now: number): number | null {
  if (session.session_duration != null && session.session_duration >= 0) return session.session_duration
  if (!isSessionActive(session)) return null
  const loginTime = parseTimestamp(session.login_time)
  return loginTime == null ? null : Math.max(0, (now - loginTime) / 1000)
}

export function isSessionActive(session: SessionRow): boolean {
  return session.logout_time == null
}

export interface ActivitySummary {
  sessionCount: number
  totalDuration: number
  averageDuration: number
  averagePing: number | null
}

export function getActivitySummary(sessions: SessionRow[], now = Date.now()): ActivitySummary {
  const recentSessions = sessions.filter((session) => {
    const loginTime = parseTimestamp(session.login_time)
    return loginTime != null && loginTime >= now - THIRTY_DAYS_MS && loginTime <= now
  })
  const durations = recentSessions
    .map((session) => durationForSummary(session, now))
    .filter((duration): duration is number => duration != null)
  const pings = recentSessions
    .map((session) => session.avg_ping)
    .filter((ping): ping is number => ping != null)
  const totalDuration = durations.reduce((total, duration) => total + duration, 0)

  return {
    sessionCount: recentSessions.length,
    totalDuration,
    averageDuration: durations.length ? totalDuration / durations.length : 0,
    averagePing: pings.length ? pings.reduce((total, ping) => total + ping, 0) / pings.length : null,
  }
}

function compareDatesDesc(a: string | null, b: string | null): number {
  const left = parseTimestamp(a)
  const right = parseTimestamp(b)
  if (left == null && right == null) return 0
  if (left == null) return 1
  if (right == null) return -1
  return right - left
}

export function sortIpRows(rows: IpRow[]): IpRow[] {
  return [...rows].sort((a, b) => compareDatesDesc(a.last_seen, b.last_seen) || b.id - a.id)
}

export function sortNameRows(rows: NameRow[]): NameRow[] {
  return [...rows].sort((a, b) => compareDatesDesc(a.last_seen, b.last_seen) || compareDatesDesc(a.first_seen, b.first_seen) || b.id - a.id)
}

export function sortKillRows(rows: KillRow[]): KillRow[] {
  return [...rows].sort((a, b) => b.kills_count - a.kills_count || a.id - b.id)
}

export interface RoleActionGroup {
  role: string
  actions: RoleActionRow[]
}

export function groupRoleActions(rows: RoleActionRow[]): RoleActionGroup[] {
  const groups = new Map<string, RoleActionRow[]>()
  for (const row of rows) groups.set(row.role, [...(groups.get(row.role) ?? []), row])
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([role, actions]) => ({
      role,
      actions: actions.sort((a, b) => b.value - a.value || a.id - b.id),
    }))
}
