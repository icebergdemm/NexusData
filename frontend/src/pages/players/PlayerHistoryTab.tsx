import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { formatBrowserDate } from "@/lib/format-date"
import type { PlayerDetailResponse, SessionRow } from "./detail-types"
import { getActivitySummary, groupRoleActions, isSessionActive, sortIpRows, sortKillRows, sortNameRows } from "./history-utils"

interface PlayerHistoryTabProps {
  data: PlayerDetailResponse
}

function fmtDate(iso: string | null): string {
  return formatBrowserDate(iso)
}

function fmtDuration(seconds: number | null): string {
  if (!seconds) return "-"
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return h > 0 ? `${h}ч ${m}м` : `${m}м`
}

function fmtPeriod(firstSeen: string | null, lastSeen: string | null): string {
  if (!firstSeen && !lastSeen) return "-"
  if (!firstSeen || firstSeen === lastSeen) return fmtDate(firstSeen ?? lastSeen)
  return `${fmtDate(firstSeen)} — ${fmtDate(lastSeen)}`
}

function SessionRowView({ session }: { session: SessionRow }) {
  const active = isSessionActive(session)
  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-3 border-b py-2 last:border-0">
      <span className="font-medium">#{session.session_number}</span>
      <span className="min-w-0 truncate text-muted-foreground">{fmtDate(session.login_time)}</span>
      <span className="text-right text-muted-foreground">{active ? "идёт" : fmtDuration(session.session_duration)}</span>
      {active ? <Badge className="justify-self-end">Онлайн</Badge> : <span className="text-right text-xs text-muted-foreground">{session.avg_ping != null ? `${Math.round(session.avg_ping)} мс` : "—"}</span>}
    </div>
  )
}

export function PlayerHistoryTab({ data }: PlayerHistoryTabProps) {
  const [showAllSessions, setShowAllSessions] = useState(false)
  const [showAllNames, setShowAllNames] = useState(false)
  const [showAllIps, setShowAllIps] = useState(false)
  const [showAllTelemetry, setShowAllTelemetry] = useState(false)
  const activity = getActivitySummary(data.sessions)
  const sessions = showAllSessions ? data.sessions : data.sessions.slice(0, 10)
  const sortedNames = sortNameRows(data.names)
  const names = showAllNames ? sortedNames : sortedNames.slice(0, 10)
  const ips = sortIpRows(data.ips)
  const visibleIps = showAllIps ? ips : ips.slice(0, 8)
  const kills = sortKillRows(data.kills)
  const visibleKills = showAllTelemetry ? kills : kills.slice(0, 8)
  const roleGroups = groupRoleActions(data.role_actions)
  const hasMoreTelemetry = kills.length > visibleKills.length || roleGroups.some(({ actions }) => actions.length > (showAllTelemetry ? actions.length : 6))

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <Card className="h-full">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Активность</CardTitle>
          <p className="text-xs text-muted-foreground">Последние 30 дней</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-md border bg-muted/20 p-3"><div className="text-lg font-semibold">{activity.sessionCount}</div><div className="text-xs text-muted-foreground">Сессий</div></div>
            <div className="rounded-md border bg-muted/20 p-3"><div className="text-lg font-semibold">{fmtDuration(activity.totalDuration)}</div><div className="text-xs text-muted-foreground">Игрового времени</div></div>
            <div className="rounded-md border bg-muted/20 p-3"><div className="text-lg font-semibold">{fmtDuration(activity.averageDuration)}</div><div className="text-xs text-muted-foreground">Средняя сессия</div></div>
            <div className="rounded-md border bg-muted/20 p-3"><div className="text-lg font-semibold">{activity.averagePing == null ? "—" : `${Math.round(activity.averagePing)} мс`}</div><div className="text-xs text-muted-foreground">Средний пинг</div></div>
          </div>
          <div className="text-xs font-medium text-muted-foreground">Последние сессии</div>
          <div className="text-sm">
            {sessions.map((session) => <SessionRowView key={session.id} session={session} />)}
            {!data.sessions.length && <div className="py-2 text-muted-foreground">Нет данных</div>}
          </div>
          {data.sessions.length > 10 && <Button variant="outline" size="sm" onClick={() => setShowAllSessions((value) => !value)}>{showAllSessions ? "Свернуть" : `Показать все (${data.sessions.length})`}</Button>}
        </CardContent>
      </Card>

      <Card className="h-full">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Идентификация</CardTitle>
          <p className="text-xs text-muted-foreground">Ники и сетевые следы</p>
        </CardHeader>
        <CardContent className="space-y-5">
          <div>
            <div className="mb-2 flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground"><span>Никнеймы</span><span>{data.names.length}</span></div>
            <div className="text-sm">
              {names.map((name) => (
                <div key={name.id} className="flex items-start justify-between gap-3 border-b py-2 last:border-0">
                  <div className="min-w-0"><div className="truncate">{name.nickname} {name.nickname === data.player.player_last_time_name && <Badge variant="outline" className="ml-1 align-middle">Текущий</Badge>}</div><div className="text-xs text-muted-foreground">{fmtPeriod(name.first_seen, name.last_seen)}</div></div>
                </div>
              ))}
              {!data.names.length && <div className="py-2 text-muted-foreground">Нет данных</div>}
            </div>
            {data.names.length > 10 && <Button variant="ghost" size="sm" className="mt-2 px-0" onClick={() => setShowAllNames((value) => !value)}>{showAllNames ? "Свернуть" : `Показать все (${data.names.length})`}</Button>}
          </div>
          <div>
            <div className="mb-2 flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground"><span>IP-адреса</span><span>{data.ips.length}</span></div>
            <div className="text-sm">
              {visibleIps.map((ip) => <div key={ip.id} className="border-b py-2 last:border-0"><div className="flex justify-between gap-3"><span className="font-mono">{ip.ip}</span><span className="text-right text-xs text-muted-foreground">{fmtPeriod(ip.first_seen, ip.last_seen)}</span></div><div className="mt-0.5 font-mono text-xs text-muted-foreground">{ip.subnet || "Подсеть не определена"}</div></div>)}
              {!data.ips.length && <div className="py-2 text-muted-foreground">Нет данных</div>}
            </div>
            {data.ips.length > 8 && <Button variant="ghost" size="sm" className="mt-2 px-0" onClick={() => setShowAllIps((value) => !value)}>{showAllIps ? "Свернуть" : `Показать все (${data.ips.length})`}</Button>}
          </div>
        </CardContent>
      </Card>

      <Card className="h-full md:col-span-2 xl:col-span-1">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Телеметрия</CardTitle>
          <p className="text-xs text-muted-foreground">Агрегированные игровые действия</p>
        </CardHeader>
        <CardContent className="space-y-5">
          <div>
            <div className="mb-2 flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground"><span>Убийства монстров</span><span>{data.kills.length}</span></div>
            <div className="text-sm">
              {visibleKills.map((kill) => <div key={kill.id} className="flex justify-between gap-3 border-b py-2 last:border-0"><span className="min-w-0 truncate font-mono">{kill.monster_type}</span><span className="text-muted-foreground">{kill.kills_count}</span></div>)}
              {!data.kills.length && <div className="py-2 text-muted-foreground">Нет данных</div>}
            </div>
          </div>
          <div>
            <div className="mb-2 flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground"><span>Действия по ролям</span><span>{data.role_actions.length}</span></div>
            <div className="space-y-3 text-sm">
              {roleGroups.map(({ role, actions }) => <div key={role}><div className="mb-1 font-medium">{role}</div>{(showAllTelemetry ? actions : actions.slice(0, 6)).map((action) => <div key={action.id} className="flex justify-between gap-3 border-b py-1.5 last:border-0"><span className="min-w-0 truncate font-mono text-xs">{action.action_type}</span><span className="text-muted-foreground">{action.value}</span></div>)}</div>)}
              {!data.role_actions.length && <div className="py-2 text-muted-foreground">Нет данных</div>}
            </div>
          </div>
          {hasMoreTelemetry && <Button variant="outline" size="sm" onClick={() => setShowAllTelemetry((value) => !value)}>{showAllTelemetry ? "Свернуть" : "Показать все"}</Button>}
        </CardContent>
      </Card>
    </div>
  )
}
