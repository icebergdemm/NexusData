/** Мониторинг производительности и процессов — только для суперпользователей.
 *
 *  Три источника сигналов (см. services_perf_monitor.py):
 *   - event-loop lag: сайт буквально не отвечал никому N секунд — самый
 *     прямой индикатор "полного зависания";
 *   - медленные HTTP-запросы (конкретный path/method дольше порога);
 *   - CPU/RAM процесса и системы, сэмплируются фоново раз в 30с.
 *  Плюс живой снимок (/api/v1/admin/health, уже существовавший, но нигде не
 *  показанный) и последние фоновые задачи планировщика — чтобы "все
 *  процессы, не только фоновые" было видно в одном месте.
 */
import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Line } from "react-chartjs-2"
import { Activity, AlertTriangle, Cpu, Gauge, MemoryStick, ShieldAlert, Timer, Trash2 } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { PageHeader } from "@/components/layout/PageHeader"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"
import { baseChartOptions, lineDatasetDefaults, CHART_COLORS } from "@/lib/chart-theme"

interface HealthResponse {
  process?: { pid: number; memory_rss_mb: number; cpu_percent: number; threads: number; uptime_secs: number }
  system?: { ram_pct: number; cpu_pct: number; cpu_cores: number }
  database?: { status: string; latency_ms: number }
  scheduler?: { available: boolean; workers: number; queue_size: number; running: number; running_tasks: string[]; recent_tasks: TaskSummary[] }
}

interface TaskSummary {
  task_id: string
  status: string
  duration_ms: number | null
  finished_at: string | null
  error: string | null
}

interface PerfSample {
  ts: string
  proc_cpu_pct: number
  sys_cpu_pct: number
  rss_mb: number
  sys_ram_pct: number
  threads: number
  event_loop_lag_ms: number
}

interface PerfAnomaly {
  id: number
  kind: string
  value: number
  path: string | null
  method: string | null
  detail: string | null
  created_at: string | null
}

interface AnomaliesResponse {
  anomalies: PerfAnomaly[]
  total: number
  page: number
  pages: number
  retention_days: number
  thresholds: { event_loop_lag_s: number; slow_request_s: number; high_cpu_pct: number; high_memory_pct: number }
}

const KIND_LABEL: Record<string, string> = {
  event_loop_lag: "Зависание сайта",
  slow_request: "Медленный запрос",
  high_cpu: "Высокая нагрузка CPU",
  high_memory: "Высокое потребление RAM",
}
const KIND_VARIANT: Record<string, "destructive" | "secondary"> = {
  event_loop_lag: "destructive", slow_request: "secondary", high_cpu: "secondary", high_memory: "secondary",
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "-"
}

function fmtUptime(secs: number): string {
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  return `${h}ч ${m}м`
}

// Статические классы (не собираются из шаблонной строки) — Tailwind JIT
// генерирует только те классы, что встречаются в исходниках буквально.
const ACCENT_CLASSES: Record<string, string> = {
  primary: "bg-primary/15 text-primary",
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning",
  info: "bg-info/15 text-info",
}

function Tile({ label, value, icon: Icon, accent, warn }: {
  label: string; value: string; icon: typeof Cpu; accent: string; warn?: boolean
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3">
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${warn ? "bg-destructive/15 text-destructive" : ACCENT_CLASSES[accent]}`}>
          <Icon className="h-5 w-5" />
        </div>
        <div>
          <div className={`text-2xl font-bold leading-none ${warn ? "text-destructive" : ""}`}>{value}</div>
          <div className="mt-1 text-xs text-muted-foreground">{label}</div>
        </div>
      </CardContent>
    </Card>
  )
}

export function PerfMonitorPage() {
  const { data: admin } = useCurrentAdmin()
  const [kind, setKind] = useState("all")
  const [page, setPage] = useState(1)
  const [clearOpen, setClearOpen] = useState(false)
  const queryClient = useQueryClient()
  const enabled = !!admin?.is_superuser

  const { data: health, isLoading: healthLoading } = useQuery({
    queryKey: ["perf-health"],
    queryFn: () => api.get<HealthResponse>("/api/v1/admin/health"),
    enabled, refetchInterval: 15000,
  })

  const { data: history } = useQuery({
    queryKey: ["perf-history"],
    queryFn: () => api.get<{ samples: PerfSample[] }>("/api/v1/admin/perf/history"),
    enabled, refetchInterval: 15000,
  })

  const { data: anomalies, isLoading: anomaliesLoading } = useQuery({
    queryKey: ["perf-anomalies", kind, page],
    queryFn: () => api.get<AnomaliesResponse>(`/api/v1/admin/perf/anomalies?${new URLSearchParams({
      ...(kind !== "all" ? { kind } : {}), page: String(page),
    })}`),
    enabled,
  })

  const clearMutation = useMutation({
    mutationFn: () => api.delete<{ deleted: number }>("/api/v1/admin/perf/anomalies"),
    onSuccess: (res) => {
      toast.success(`Журнал очищен (${res.deleted})`)
      setClearOpen(false)
      queryClient.invalidateQueries({ queryKey: ["perf-anomalies"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (admin && !admin.is_superuser) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-2 py-12 text-center text-muted-foreground">
          <ShieldAlert className="size-8" />
          Мониторинг производительности доступен только суперпользователям.
        </CardContent>
      </Card>
    )
  }

  const samples = history?.samples ?? []
  const latest = samples[samples.length - 1]
  const labels = samples.map((s) => new Date(s.ts).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }))

  return (
    <div>
      <PageHeader
        title="Мониторинг производительности"
        description="Зависания event loop, медленные запросы, нагрузка на процесс и систему — только для суперпользователей"
        icon={Activity}
      />

      {healthLoading ? (
        <div className="mb-4 grid gap-3 sm:grid-cols-5">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-20" />)}</div>
      ) : (
        <div className="mb-4 grid gap-3 sm:grid-cols-5">
          <Tile label="CPU системы" value={`${health?.system?.cpu_pct ?? "-"}%`} icon={Cpu} accent="info" warn={(health?.system?.cpu_pct ?? 0) >= 85} />
          <Tile label="RAM системы" value={`${health?.system?.ram_pct ?? "-"}%`} icon={MemoryStick} accent="warning" warn={(health?.system?.ram_pct ?? 0) >= 90} />
          <Tile label="Процесс RSS" value={`${health?.process?.memory_rss_mb?.toFixed(0) ?? "-"} МБ`} icon={Gauge} accent="primary" />
          <Tile
            label="Задержка event loop"
            value={`${latest?.event_loop_lag_ms.toFixed(0) ?? 0} мс`}
            icon={Timer}
            accent="success"
            warn={(latest?.event_loop_lag_ms ?? 0) >= (anomalies?.thresholds.event_loop_lag_s ?? 0.5) * 1000}
          />
          <Tile label="Очередь фоновых задач" value={String(health?.scheduler?.queue_size ?? "-")} icon={Activity} accent="info" />
        </div>
      )}

      {samples.length > 1 && (
        <Card className="mb-4 overflow-hidden py-0">
          <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">CPU / RAM за последние ~2 часа</CardTitle></CardHeader>
          <CardContent className="pt-4" style={{ height: 220 }}>
            <Line
              options={baseChartOptions}
              data={{
                labels,
                datasets: [
                  { label: "CPU системы, %", data: samples.map((s) => s.sys_cpu_pct), borderColor: CHART_COLORS[1], ...lineDatasetDefaults },
                  { label: "RAM системы, %", data: samples.map((s) => s.sys_ram_pct), borderColor: CHART_COLORS[0], backgroundColor: CHART_COLORS[0] + "26", fill: true, ...lineDatasetDefaults },
                ],
              }}
            />
          </CardContent>
        </Card>
      )}

      {health?.scheduler && health.scheduler.recent_tasks.length > 0 && (
        <Card className="mb-4 overflow-hidden py-0">
          <CardHeader className="border-b bg-muted/30 py-3">
            <CardTitle className="text-sm font-semibold">
              Последние фоновые задачи ({health.scheduler.workers} воркера, {health.scheduler.running} выполняется)
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader><TableRow><TableHead>Задача</TableHead><TableHead>Статус</TableHead><TableHead>Длительность</TableHead><TableHead>Завершена</TableHead></TableRow></TableHeader>
              <TableBody>
                {health.scheduler.recent_tasks.map((t) => (
                  <TableRow key={t.task_id}>
                    <TableCell className="font-mono text-xs">{t.task_id}</TableCell>
                    <TableCell>
                      <Badge variant={t.status === "error" || t.status === "timeout" ? "destructive" : "secondary"}>{t.status}</Badge>
                    </TableCell>
                    <TableCell className={(t.duration_ms ?? 0) > 5000 ? "text-warning" : "text-muted-foreground"}>
                      {t.duration_ms != null ? `${t.duration_ms} мс` : "-"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{fmtDate(t.finished_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select value={kind} onValueChange={(v) => { setKind(v); setPage(1) }}>
          <SelectTrigger className="w-56"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все типы аномалий</SelectItem>
            {Object.entries(KIND_LABEL).map(([k, label]) => <SelectItem key={k} value={k}>{label}</SelectItem>)}
          </SelectContent>
        </Select>
        {anomalies && (
          <>
            <Badge variant="secondary" className="text-sm font-normal">{anomalies.total} записей · хранится {anomalies.retention_days} дн.</Badge>
            <Button size="sm" variant="destructive" className="ml-auto" onClick={() => setClearOpen(true)}>
              <Trash2 className="size-3.5" /> Очистить журнал
            </Button>
          </>
        )}
      </div>

      <Card className="overflow-hidden py-0">
        <CardContent className="p-0">
          {anomaliesLoading ? (
            <div className="space-y-2 p-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Тип</TableHead>
                  <TableHead>Значение</TableHead>
                  <TableHead>Путь</TableHead>
                  <TableHead>Детали</TableHead>
                  <TableHead className="text-right">Время</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {anomalies?.anomalies.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell>
                      <span className="inline-flex items-center gap-1.5">
                        {a.kind === "event_loop_lag" && <AlertTriangle className="size-3.5 text-destructive" />}
                        <Badge variant={KIND_VARIANT[a.kind] ?? "secondary"}>{KIND_LABEL[a.kind] ?? a.kind}</Badge>
                      </span>
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {a.kind === "event_loop_lag" || a.kind === "slow_request" ? `${a.value.toFixed(2)} с` : `${a.value.toFixed(1)}%`}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {a.path ? `${a.method ?? ""} ${a.path}` : "-"}
                    </TableCell>
                    <TableCell className="max-w-52 truncate text-xs text-muted-foreground" title={a.detail ?? undefined}>{a.detail ?? "-"}</TableCell>
                    <TableCell className="text-right text-xs whitespace-nowrap text-muted-foreground">{fmtDate(a.created_at)}</TableCell>
                  </TableRow>
                ))}
                {!anomalies?.anomalies.length && (
                  <TableRow><TableCell colSpan={5} className="py-10 text-center text-muted-foreground">Аномалий не зафиксировано — хорошая новость</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          )}
          {anomalies && anomalies.pages > 1 && (
            <div className="flex items-center justify-center gap-2 border-t p-3">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Назад</Button>
              <span className="text-sm text-muted-foreground">Страница {anomalies.page} из {anomalies.pages}</span>
              <Button variant="outline" size="sm" disabled={page >= anomalies.pages} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
            </div>
          )}
        </CardContent>
      </Card>

      {health?.process && (
        <p className="mt-3 text-xs text-muted-foreground">
          PID {health.process.pid} · аптайм {fmtUptime(health.process.uptime_secs)} · потоков {health.process.threads}
          {health.database && <> · БД {health.database.status === "ok" ? `${health.database.latency_ms} мс` : "недоступна"}</>}
        </p>
      )}

      <ConfirmDialog
        open={clearOpen}
        onOpenChange={setClearOpen}
        title="Очистить журнал аномалий?"
        description="Удалит все записи безвозвратно. Новые аномалии будут писаться заново."
        confirmLabel="Очистить"
        onConfirm={() => clearMutation.mutate()}
      />
    </div>
  )
}
