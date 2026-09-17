import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { formatBrowserDate } from "@/lib/format-date"
import type { PlayerDetailResponse, SessionRow } from "./detail-types"
import { getActivitySummary, getTelemetrySummary, groupRoleActions, isSessionActive, sortIpRows, sortKillRows, sortNameRows } from "./history-utils"

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
  const activity = getActivitySummary(data.sessions)
  const sortedNames = sortNameRows(data.names)
  const ips = sortIpRows(data.ips)
  const kills = sortKillRows(data.kills)
  const roleGroups = groupRoleActions(data.role_actions)
  const telemetry = getTelemetrySummary(kills, data.role_actions)

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <Card className="h-full">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Активность</CardTitle>
          <p className="text-xs text-muted-foreground">Последние 30 дней</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <section className="rounded-lg border bg-muted/20 p-3">
            <div className="grid grid-cols-2 gap-2">
            <div className="rounded-md border bg-muted/20 p-3"><div className="text-lg font-semibold">{activity.sessionCount}</div><div className="text-xs text-muted-foreground">Сессий</div></div>
            <div className="rounded-md border bg-muted/20 p-3"><div className="text-lg font-semibold">{fmtDuration(activity.totalDuration)}</div><div className="text-xs text-muted-foreground">Игрового времени</div></div>
            <div className="rounded-md border bg-muted/20 p-3"><div className="text-lg font-semibold">{fmtDuration(activity.averageDuration)}</div><div className="text-xs text-muted-foreground">Средняя сессия</div></div>
            <div className="rounded-md border bg-muted/20 p-3"><div className="text-lg font-semibold">{activity.averagePing == null ? "—" : `${Math.round(activity.averagePing)} мс`}</div><div className="text-xs text-muted-foreground">Средний пинг</div></div>
            </div>
          </section>
          <section className="rounded-lg border bg-muted/10 p-3">
            <div className="mb-2 flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground"><span>Последние сессии</span><span>{data.sessions.length}</span></div>
            <ScrollArea className="h-72">
              <div className="pr-3 text-sm">
                {data.sessions.map((session) => <SessionRowView key={session.id} session={session} />)}
                {!data.sessions.length && <div className="py-2 text-muted-foreground">Нет данных</div>}
              </div>
            </ScrollArea>
          </section>
        </CardContent>
      </Card>

      <Card className="h-full">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Идентификация</CardTitle>
          <p className="text-xs text-muted-foreground">Ники и сетевые следы</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <section className="rounded-lg border bg-muted/10 p-3">
            <div className="mb-2 flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground"><span>Никнеймы</span><span>{data.names.length}</span></div>
            <ScrollArea className="h-56">
              <div className="pr-3 text-sm">
              {sortedNames.map((name) => (
                <div key={name.id} className="flex items-start justify-between gap-3 border-b py-2 last:border-0">
                  <div className="min-w-0"><div className="truncate">{name.nickname} {name.nickname === data.player.player_last_time_name && <Badge variant="outline" className="ml-1 align-middle">Текущий</Badge>}</div><div className="text-xs text-muted-foreground">{fmtPeriod(name.first_seen, name.last_seen)}</div></div>
                </div>
              ))}
              {!data.names.length && <div className="py-2 text-muted-foreground">Нет данных</div>}
              </div>
            </ScrollArea>
          </section>
          <section className="rounded-lg border bg-muted/10 p-3">
            <div className="mb-2 flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground"><span>IP-адреса</span>{data.can_view_ip ? <span>{data.ips.length}</span> : <Badge variant="outline">Скрыто</Badge>}</div>
            <ScrollArea className="h-56">
              <div className="pr-3 text-sm">
              {data.can_view_ip && ips.map((ip) => <div key={ip.id} className="border-b py-2 last:border-0"><div className="flex justify-between gap-3"><span className="font-mono">{ip.ip}</span><span className="text-right text-xs text-muted-foreground">{fmtPeriod(ip.first_seen, ip.last_seen)}</span></div><div className="mt-0.5 font-mono text-xs text-muted-foreground">{ip.subnet || "Подсеть не определена"}</div></div>)}
              {!data.can_view_ip && <div className="py-2 text-muted-foreground">Доступ к IP-адресам ограничен.</div>}
              {data.can_view_ip && !data.ips.length && <div className="py-2 text-muted-foreground">Нет данных</div>}
              </div>
            </ScrollArea>
          </section>
        </CardContent>
      </Card>

      <Card className="h-full md:col-span-2 xl:col-span-1">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Телеметрия</CardTitle>
          <p className="text-xs text-muted-foreground">Агрегированные игровые действия</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <section className="rounded-lg border bg-muted/20 p-3">
            <div className="grid grid-cols-2 gap-2">
              <div><div className="text-2xl font-semibold">{telemetry.totalMonsterKills}</div><div className="text-xs text-muted-foreground">Убийств монстров</div></div>
              <div><div className="text-2xl font-semibold">{telemetry.monsterTypes}</div><div className="text-xs text-muted-foreground">Типов монстров</div></div>
              <div><div className="text-2xl font-semibold">{telemetry.roles}</div><div className="text-xs text-muted-foreground">Ролей с действиями</div></div>
              <div><div className="text-2xl font-semibold">{telemetry.actionRecords}</div><div className="text-xs text-muted-foreground">Агрегатов действий</div></div>
            </div>
          </section>
          <details className="group rounded-lg border bg-muted/10">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-3 text-sm marker:content-none">
              <span><span className="block font-medium">Технические агрегаты</span><span className="block text-xs text-muted-foreground">Типы монстров и действия по ролям</span></span>
              <span className="text-xs text-muted-foreground group-open:hidden">Раскрыть</span><span className="hidden text-xs text-muted-foreground group-open:inline">Скрыть</span>
            </summary>
            <div className="space-y-4 border-t p-3">
              <section>
                <div className="mb-2 flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground"><span>Убийства монстров</span><span>{kills.length}</span></div>
                <ScrollArea className="h-44 rounded-md border bg-background/30">
                  <div className="px-3 text-sm">{kills.map((kill) => <div key={kill.id} className="flex justify-between gap-3 border-b py-2 last:border-0"><span className="min-w-0 truncate font-mono">{kill.monster_type}</span><span className="text-muted-foreground">{kill.kills_count}</span></div>)}{!kills.length && <div className="py-2 text-muted-foreground">Нет данных</div>}</div>
                </ScrollArea>
              </section>
              <section>
                <div className="mb-2 flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground"><span>Действия по ролям</span><span>{data.role_actions.length}</span></div>
                <ScrollArea className="h-64 rounded-md border bg-background/30">
                  <div className="space-y-3 px-3 text-sm">{roleGroups.map(({ role, actions }) => <div key={role}><div className="mb-1 font-medium">{role}</div>{actions.map((action) => <div key={action.id} className="flex justify-between gap-3 border-b py-1.5 last:border-0"><span className="min-w-0 truncate font-mono text-xs">{action.action_type}</span><span className="text-muted-foreground">{action.value}</span></div>)}</div>)}{!data.role_actions.length && <div className="py-2 text-muted-foreground">Нет данных</div>}</div>
                </ScrollArea>
              </section>
            </div>
          </details>
        </CardContent>
      </Card>
    </div>
  )
}
