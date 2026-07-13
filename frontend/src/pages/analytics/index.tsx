import { useQuery } from "@tanstack/react-query"
import { Line, Bar } from "react-chartjs-2"
import { UserPlus, Repeat, TrendingUp, Activity } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { baseChartOptions, barDatasetDefaults, lineDatasetDefaults, CHART_COLORS } from "@/lib/chart-theme"

interface OnlineTimeline { labels: string[]; avg: number[]; peak: number[]; total_snapshots: number }
interface RolePopularity { roles: string[]; counts: number[] }
interface SubmarinePopularity { submarines: string[]; counts: number[] }
interface PeakHours { labels: string[]; avg: number[]; peak: number[] }
interface SessionDuration { labels: string[]; counts: number[]; total_sessions: number }
interface ServerComparison { [server: string]: { avg_online: number; peak_online: number; rounds_week: number } }
interface PlayerRetention { total_new_30d: number; returning_30d: number; retention_pct: number; active_this_week: number }
interface TopPlayers { players: { name: string; value: number; unit: string }[] }

function useAnalytics<T>(key: string, path: string) {
  return useQuery({
    queryKey: ["analytics", key],
    queryFn: () => api.get<T>(path),
    staleTime: 60_000,
  })
}

function ChartCard({ title, height = 260, children }: { title: string; height?: number; children: React.ReactNode }) {
  return (
    <Card className="overflow-hidden">
      <CardHeader className="border-b bg-muted/30 py-3">
        <CardTitle className="text-sm font-semibold">{title}</CardTitle>
      </CardHeader>
      <CardContent className="pt-4" style={{ height: height + 16 }}>{children}</CardContent>
    </Card>
  )
}

const noLegend = { legend: { display: false } }

export function AnalyticsPage() {
  const online = useAnalytics<OnlineTimeline>("online-timeline", "/api/v1/analytics/online-timeline")
  const roles = useAnalytics<RolePopularity>("role-popularity", "/api/v1/analytics/role-popularity")
  const subs = useAnalytics<SubmarinePopularity>("submarine-popularity", "/api/v1/analytics/submarine-popularity")
  const peak = useAnalytics<PeakHours>("peak-hours", "/api/v1/analytics/peak-hours")
  const duration = useAnalytics<SessionDuration>("session-duration", "/api/v1/analytics/session-duration")
  const servers = useAnalytics<ServerComparison>("server-comparison", "/api/v1/analytics/server-comparison")
  const retention = useAnalytics<PlayerRetention>("player-retention", "/api/v1/analytics/player-retention")
  const topPlayers = useAnalytics<TopPlayers>("top-players", "/api/v1/analytics/top-players?metric=playtime&period=7d")

  const isLoading = [online, roles, subs, peak, duration, servers, retention, topPlayers].some((q) => q.isLoading)
  if (isLoading) {
    return <div className="grid gap-4 md:grid-cols-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-72" />)}</div>
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Новых за 30д" value={retention.data?.total_new_30d ?? 0} icon={UserPlus} accent="info" />
        <StatTile label="Вернулись (30д)" value={retention.data?.returning_30d ?? 0} icon={Repeat} accent="success" />
        <StatTile label="Удержание" value={`${retention.data?.retention_pct ?? 0}%`} icon={TrendingUp} accent="warning" />
        <StatTile label="Активны на неделе" value={retention.data?.active_this_week ?? 0} icon={Activity} accent="primary" />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <ChartCard title="Онлайн (за 6ч, среднее/пик)">
          <Line
            options={baseChartOptions}
            data={{
              labels: online.data?.labels ?? [],
              datasets: [
                { label: "Среднее", data: online.data?.avg ?? [], borderColor: CHART_COLORS[0], backgroundColor: CHART_COLORS[0] + "26", fill: true, ...lineDatasetDefaults },
                { label: "Пик", data: online.data?.peak ?? [], borderColor: CHART_COLORS[1], ...lineDatasetDefaults },
              ],
            }}
          />
        </ChartCard>

        <ChartCard title="Пиковые часы (14 дней)">
          <Line
            options={baseChartOptions}
            data={{
              labels: peak.data?.labels ?? [],
              datasets: [
                { label: "Среднее", data: peak.data?.avg ?? [], borderColor: CHART_COLORS[0], ...lineDatasetDefaults },
                { label: "Пик", data: peak.data?.peak ?? [], borderColor: CHART_COLORS[1], ...lineDatasetDefaults },
              ],
            }}
          />
        </ChartCard>

        <ChartCard title="Популярность ролей">
          <Bar
            options={{ ...baseChartOptions, plugins: { ...baseChartOptions.plugins, ...noLegend } }}
            data={{
              labels: roles.data?.roles ?? [],
              datasets: [{ data: roles.data?.counts ?? [], backgroundColor: (roles.data?.roles ?? []).map((_, i) => CHART_COLORS[i % CHART_COLORS.length]), ...barDatasetDefaults }],
            }}
          />
        </ChartCard>

        <ChartCard title="Популярность кораблей (30д)">
          <Bar
            options={{ ...baseChartOptions, indexAxis: "y" as const, plugins: { ...baseChartOptions.plugins, ...noLegend } }}
            data={{
              labels: subs.data?.submarines ?? [],
              datasets: [{ data: subs.data?.counts ?? [], backgroundColor: CHART_COLORS[0], ...barDatasetDefaults }],
            }}
          />
        </ChartCard>

        <ChartCard title="Длительность сессий (30д)">
          <Bar
            options={{ ...baseChartOptions, plugins: { ...baseChartOptions.plugins, ...noLegend } }}
            data={{
              labels: duration.data?.labels ?? [],
              datasets: [{ data: duration.data?.counts ?? [], backgroundColor: CHART_COLORS[2], ...barDatasetDefaults }],
            }}
          />
        </ChartCard>

        <ChartCard title="Топ игроков по наигранному (7д)">
          <Bar
            options={{ ...baseChartOptions, indexAxis: "y" as const, plugins: { ...baseChartOptions.plugins, ...noLegend } }}
            data={{
              labels: topPlayers.data?.players.map((p) => p.name) ?? [],
              datasets: [{ data: topPlayers.data?.players.map((p) => p.value) ?? [], backgroundColor: CHART_COLORS[3], ...barDatasetDefaults }],
            }}
          />
        </ChartCard>
      </div>

      <Card>
        <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">Сравнение серверов (7 дней)</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-2 gap-4 pt-4">
          {Object.entries(servers.data ?? {}).map(([sn, s]) => (
            <div key={sn} className="rounded-lg border bg-muted/20 p-4">
              <div className="mb-3 font-semibold">{sn}</div>
              <div className="grid grid-cols-3 gap-2 text-center text-sm">
                <div><div className="text-lg font-bold text-primary">{s.avg_online}</div><div className="text-xs text-muted-foreground">Ср. онлайн</div></div>
                <div><div className="text-lg font-bold text-success">{s.peak_online}</div><div className="text-xs text-muted-foreground">Пик</div></div>
                <div><div className="text-lg font-bold text-warning">{s.rounds_week}</div><div className="text-xs text-muted-foreground">Раундов</div></div>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  )
}

const ACCENT_CLASSES: Record<string, string> = {
  primary: "bg-primary/15 text-primary",
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning",
  info: "bg-info/15 text-info",
}

function StatTile({ label, value, icon: Icon, accent }: { label: string; value: number | string; icon: typeof UserPlus; accent: string }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 pt-6">
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${ACCENT_CLASSES[accent]}`}>
          <Icon className="h-5 w-5" />
        </div>
        <div>
          <div className="text-2xl font-bold leading-none">{value}</div>
          <div className="mt-1 text-xs text-muted-foreground">{label}</div>
        </div>
      </CardContent>
    </Card>
  )
}
