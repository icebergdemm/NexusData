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
import { Activity, AlertTriangle, Cpu, Gauge, ListTree, MemoryStick, Pause, Play, RotateCcw, ShieldAlert, Square, Timer, Trash2 } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { PageHeader } from "@/components/layout/PageHeader"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
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
  started_at?: string | null
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

// ── Диспетчер задач (live) ──────────────────────────────────────────────────
interface TMProcess {
  pid: number
  ppid: number
  name: string
  status: string
  cpu_pct: number
  cpu_total_s: number
  mem_rss_mb: number
  mem_pct: number
  threads: number
  cmd: string
  is_python: boolean
  is_own: boolean
  own_child: boolean
}

interface TMThread {
  tid: number
  name: string
  cpu_pct: number
  cpu_total_s: number
  state: string
  stack: string
  is_main: boolean
}

interface TMPeriodic {
  task_id: string
  interval_secs: number
  priority: string
  timeout_secs: number | null
  due_in_secs: number
  running: boolean
  last_status: string | null
  last_duration_ms: number | null
}

interface TMScheduler {
  available: boolean
  workers: number
  queue_size: number
  active: TaskSummary[]
  periodic: TMPeriodic[]
  recent: TaskSummary[]
}

interface TMSubsystem {
  name: string
  running: boolean
  detail: string
  source: string
  control?: string | null
}

interface TaskManagerResponse {
  generated_at: string
  own_pid: number
  processes: TMProcess[]
  app_threads: TMThread[]
  app_rss_mb: number
  scheduler: TMScheduler
  subsystems: TMSubsystem[]
}

const PROC_STATUS_LABEL: Record<string, string> = {
  running: "выполняется",
  sleeping: "спит",
  "disk-sleep": "ожидает диск",
  stopped: "остановлен",
  zombie: "зомби",
  idle: "простой",
  waiting: "ожидает",
  wakep: "пробуждение",
  waking: "пробуждение",
}
const PROC_STATUS_VARIANT: Record<string, "destructive" | "secondary" | "default" | "outline"> = {
  running: "default",
  sleeping: "secondary",
  stopped: "destructive",
  zombie: "destructive",
  "disk-sleep": "secondary",
  idle: "outline",
}

function fmtDue(secs: number): string {
  if (secs <= 0) return "сейчас"
  if (secs < 90) return `${Math.round(secs)} с`
  const m = Math.round(secs / 60)
  if (m < 100) return `${m} мин`
  return `${(secs / 3600).toFixed(1)} ч`
}

function fmtInterval(secs: number): string {
  if (secs < 90) return `${secs} с`
  if (secs < 7200) return `${Math.round(secs / 60)} мин`
  if (secs < 86400 * 2) return `${Math.round(secs / 3600)} ч`
  return `${Math.round(secs / 86400)} дн.`
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
  return formatBrowserDate(iso)
}

interface CpuCaptureResult {
  seconds: number
  samples_total: number
  top: { function: string; file: string; samples: number; pct: number }[]
}

interface MemProfileResult {
  status: string
  hint?: string
  top?: { location: string; size_mb: number; count: number }[]
  growth?: { location: string; delta_mb: number }[]
}

function TaskManagerSection({ data, tab, onTab, onRefetch }: {
  data: TaskManagerResponse
  tab: string
  onTab: (v: string) => void
  onRefetch: () => void
}) {
  const activeTasks = data.scheduler.active
  const [pending, setPending] = useState<{ target: string; action: string; title: string; description: string } | null>(null)
  const [profilerSeconds, setProfilerSeconds] = useState("6")

  // HTTP control mutation — reuses one endpoint for process + subsystem rows.
  const controlMutation = useMutation({
    mutationFn: (arg: { target: string; action: string }) =>
      api.post<{ status: string; warning?: string | null }>("/api/v1/admin/perf/task-manager/control", arg),
    onSuccess: (res, arg) => {
      toast.success(`${{ suspend: "Приостановлен", resume: "Возобновлён", kill: "Завершён", terminate: "Остановлен", restart: "Перезапуск отправлен", stop: "Остановлена", start: "Запущена" }[arg.action as string] ?? arg.action}: ${arg.target}`)
      if (res.warning) toast.warning(res.warning)
      onRefetch()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const cpuMutation = useMutation({
    mutationFn: () =>
      api.post<CpuCaptureResult>("/api/v1/admin/perf/task-manager/cpu-capture", { seconds: Number(profilerSeconds) || 6 }),
    onSuccess: (res) => toast.success(`Срез снят: ${res.samples_total} сэмплов за ${res.seconds} с`),
    onError: (e: Error) => toast.error(e.message),
  })

  const memMutation = useMutation({
    mutationFn: (action: string) =>
      api.post<MemProfileResult>("/api/v1/admin/perf/task-manager/mem-profile", { action }),
    onSuccess: (_res, action) => {
      if (action === "start") toast.success("Профайлер памяти включён — снимок чуть позже покажет и прирост")
      else if (action === "stop") toast.success("Профайлер памяти остановлен")
      else toast.success("Снимок памяти готов")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const control = (target: string, action: string, label: string, danger = true) =>
    setPending({ target, action, title: `${action === "suspend" ? "Приостановить" : action === "kill" ? "Завершить" : action === "restart" ? "Перезапустить" : action === "resume" ? "Возобновить" : action === "stop" ? "Остановить" : "Запустить"} — ${label}`,

      description: danger
        ? "Действие выполняется сразу. Всё, что нужно, будет восстановлено вручную или после перезапуска контейнера."
        : "Действие на службе: остановку нужно вернуть вручную кнопкой, либо после перезапуска бэкенда." })

  const act = () => {
    if (!pending) return
    controlMutation.mutate({ target: pending.target, action: pending.action })
    setPending(null)
  }

  return (
    <Card className="mb-4 overflow-hidden py-0" data-v2-taskmanager>
      <CardHeader className="border-b bg-muted/30 py-3">
        <CardTitle className="flex items-center gap-2 text-sm font-semibold">
          <ListTree className="size-4" /> Диспетчер задач (live, ~2 с)
          {data.scheduler.available && (
            <Badge variant="secondary" className="ml-auto font-mono font-normal">
              {activeTasks.length} выполняется · очередь {data.scheduler.queue_size} · {data.scheduler.workers} воркера
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3">
        <Tabs value={tab} onValueChange={onTab}>
          <TabsList className="mb-3 flex-wrap">
            <TabsTrigger value="processes">Процессы ({data.processes.length})</TabsTrigger>
            <TabsTrigger value="threads">Потоки API ({data.app_threads.length})</TabsTrigger>
            <TabsTrigger value="scheduler">Планировщик</TabsTrigger>
            <TabsTrigger value="subsystems">Службы ({data.subsystems.length})</TabsTrigger>
            <TabsTrigger value="profiler">Профайлер</TabsTrigger>
          </TabsList>

          <TabsContent value="processes" className="mt-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>PID</TableHead>
                  <TableHead>Процесс</TableHead>
                  <TableHead>Состояние</TableHead>
                  <TableHead className="text-right">CPU %</TableHead>
                  <TableHead className="text-right">RAM МБ</TableHead>
                  <TableHead className="text-right">Потоки</TableHead>
                  <TableHead className="text-right">Управление</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.processes.map((p) => {
                  const manageable = p.is_own || p.own_child
                  const suspended = p.status === "stopped"
                  return (
                    <TableRow key={p.pid} className={p.is_own || p.own_child ? "bg-primary/5" : ""}>
                      <TableCell className="font-mono text-xs text-muted-foreground">{p.pid}</TableCell>
                      <TableCell className="max-w-56 truncate text-sm font-medium" title={p.cmd}>
                        {p.name}
                        {p.is_own && <Badge variant="default" className="ml-2 font-normal">API</Badge>}
                        {p.own_child && !p.is_own && <Badge variant="secondary" className="ml-2 font-normal">дочерний</Badge>}
                      </TableCell>
                      <TableCell>
                        <Badge variant={PROC_STATUS_VARIANT[p.status] ?? "secondary"}>
                          {PROC_STATUS_LABEL[p.status] ?? p.status}
                        </Badge>
                      </TableCell>
                      <TableCell className={`text-right font-mono text-sm ${p.cpu_pct > 15 ? "text-warning" : ""}`}>
                        {p.cpu_pct.toFixed(1)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {p.mem_rss_mb.toFixed(1)}
                        <span className="ml-1 text-xs text-muted-foreground">({p.mem_pct.toFixed(1)}%)</span>
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs text-muted-foreground">{p.threads}</TableCell>
                      <TableCell className="text-right">
                        {manageable && (
                          <div className="inline-flex gap-1">
                            {suspended ? (
                              <Button size="sm" variant="outline" className="h-7 px-2" disabled={controlMutation.isPending}
                                onClick={() => control(`pid:${p.pid}`, "resume", `PID ${p.pid}`)}>
                                <Play className="size-3" />
                              </Button>
                            ) : (
                              <Button size="sm" variant="outline" className="h-7 px-2" disabled={controlMutation.isPending}
                                onClick={() => control(`pid:${p.pid}`, "suspend", `PID ${p.pid}`)}
                                title={p.is_own ? "API перестанет отвечать; вернёт только «update restart»" : "Пауза"}>
                                <Pause className="size-3" />
                              </Button>
                            )}
                            <Button size="sm" variant="outline" className="h-7 px-2" disabled={controlMutation.isPending}
                              onClick={() => control(`pid:${p.pid}`, "restart", `PID ${p.pid}`)}
                              title="Остановить — супервизор родителя поднимет тот же процесс">
                              <RotateCcw className="size-3" />
                            </Button>
                            <Button size="sm" variant="destructive" className="h-7 px-2" disabled={controlMutation.isPending}
                              onClick={() => control(`pid:${p.pid}`, "kill", `PID ${p.pid}`)}>
                              <Square className="size-3" />
                            </Button>
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  )
                })}
                {!data.processes.length && (
                  <TableRow><TableCell colSpan={7} className="py-8 text-center text-muted-foreground">Снимок собирается…</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </TabsContent>

          <TabsContent value="threads" className="mt-0">
            <p className="mb-2 text-xs text-muted-foreground">
              Потоки процесса API ({data.own_pid}, RSS {data.app_rss_mb.toFixed(0)} МБ). CPU потока нормализован
              на одно ядро (как top): «спит» = ~0%, busy = до 100%. Нажать на стек — полная трасса секунды снимка.
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>TID</TableHead>
                  <TableHead>Поток</TableHead>
                  <TableHead>Состояние</TableHead>
                  <TableHead className="text-right">CPU %</TableHead>
                  <TableHead className="text-right">CPU всего</TableHead>
                  <TableHead>Выполняет сейчас</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.app_threads.map((t) => (
                  <TableRow key={t.tid}>
                    <TableCell className="font-mono text-xs text-muted-foreground">{t.tid}</TableCell>
                    <TableCell className="text-sm">
                      <span className="font-medium">{t.name || "—"}</span>
                      {t.is_main && <Badge variant="secondary" className="ml-2 font-normal">event loop</Badge>}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {(PROC_STATUS_LABEL[t.state] ?? t.state) || "—"}
                    </TableCell>
                    <TableCell className={`text-right font-mono text-sm ${t.cpu_pct > 25 ? "text-warning" : ""}`}>
                      {t.cpu_pct.toFixed(1)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs text-muted-foreground">{t.cpu_total_s.toFixed(1)} с</TableCell>
                    <TableCell className="max-w-md truncate font-mono text-xs text-muted-foreground" title={t.stack || undefined}>
                      {t.stack || "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TabsContent>

          <TabsContent value="scheduler" className="mt-0">
            <div className="mb-3 flex items-center gap-2">
              <Badge variant={data.scheduler.available ? "default" : "secondary"} className="font-normal">
                {data.scheduler.available ? "работает" : "не запущен"}
              </Badge>
              <Badge variant="secondary" className="font-mono font-normal">очередь {data.scheduler.queue_size}</Badge>
              {data.scheduler.available && (
                <div className="ml-auto inline-flex gap-1">
                  {data.scheduler.available ? (
                    <Button size="sm" variant="outline" disabled={controlMutation.isPending}
                      onClick={() => control("scheduler", "stop", "Планировщик фоновых задач")}
                      title="Прекратить запуск новых и новых периодических тиков; текущие дорабатывают">
                      <Pause className="size-3" /> Пауза запусков
                    </Button>
                  ) : null}
                  <Button size="sm" variant="outline" disabled={controlMutation.isPending || data.scheduler.available}
                    onClick={() => control("scheduler", "start", "Планировщик фоновых задач")}>
                    <Play className="size-3" /> Возобновить
                  </Button>
                </div>
              )}
            </div>
            <div className="grid gap-4 xl:grid-cols-2">
              <div>
                <p className="mb-1 text-xs font-semibold text-muted-foreground">Выполняются сейчас ({activeTasks.length})</p>
                <div className="rounded-xl border">
                  {activeTasks.map((t) => (
                    <div key={t.task_id} className="flex items-center gap-2 border-b px-3 py-2 last:border-0">
                      <span className="truncate font-mono text-xs">{t.task_id}</span>
                      <span className="ml-auto text-xs text-muted-foreground">
                        {t.started_at ? `с ${new Date(t.started_at).toLocaleTimeString("ru-RU")}` : ""}
                      </span>
                    </div>
                  ))}
                  {!activeTasks.length && <div className="px-3 py-4 text-xs text-muted-foreground">Ничего не выполняется</div>}
                </div>
              </div>
              <div>
                <p className="mb-1 text-xs font-semibold text-muted-foreground">Периодические фоновые задачи</p>
                <div className="max-h-96 overflow-y-auto rounded-xl border">
                  {data.scheduler.periodic.map((p) => (
                    <div key={p.task_id} className="flex items-center gap-2 border-b px-3 py-2 text-xs last:border-0">
                      <span className={`font-mono ${p.running ? "text-success" : ""}`}>{p.task_id}</span>
                      {p.running && <Badge variant="default" className="font-normal">выполняется</Badge>}
                      <span className="ml-auto whitespace-nowrap text-muted-foreground">
                        {p.priority.toLowerCase()} · каждые {fmtInterval(p.interval_secs)} · через {fmtDue(p.due_in_secs)}
                      </span>
                      {p.last_status && (
                        <Badge variant={p.last_status === "error" || p.last_status === "timeout" ? "destructive" : "secondary"} className="whitespace-nowrap font-normal">
                          {p.last_status}{p.last_duration_ms != null ? ` ${p.last_duration_ms} мс` : ""}
                        </Badge>
                      )}
                    </div>
                  ))}
                  {!data.scheduler.periodic.length && (
                    <div className="px-3 py-4 text-xs text-muted-foreground">В этом процессе планировщик не запущен (WORKER_ROLE=web)</div>
                  )}
                </div>
              </div>
            </div>
            {data.scheduler.recent.length > 0 && (
              <div className="mt-3">
                <p className="mb-1 text-xs font-semibold text-muted-foreground">Недавно завершённые</p>
                <div className="grid gap-1 sm:grid-cols-2 xl:grid-cols-3">
                  {data.scheduler.recent.map((t) => (
                    <div key={t.task_id + (t.finished_at ?? "")} className="flex items-center gap-2 rounded-lg border px-2 py-1.5 text-xs">
                      <span className="truncate font-mono" title={t.task_id}>{t.task_id}</span>
                      <Badge variant={t.status === "error" || t.status === "timeout" ? "destructive" : "secondary"} className="ml-auto font-normal">{t.status}</Badge>
                      <span className="whitespace-nowrap text-muted-foreground">{t.duration_ms ?? "-"} мс</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </TabsContent>

          <TabsContent value="subsystems" className="mt-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Служба</TableHead>
                  <TableHead>Состояние</TableHead>
                  <TableHead>Подробности</TableHead>
                  <TableHead className="text-right">Управление</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.subsystems.map((sub) => (
                  <TableRow key={sub.name}>
                    <TableCell className="text-sm font-medium">{sub.name}</TableCell>
                    <TableCell>
                      <Badge variant={sub.running ? "default" : "secondary"}>{sub.running ? "работает" : "не запущена"}</Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{sub.detail}</TableCell>
                    <TableCell className="text-right">
                      {sub.control ? (
                        <div className="inline-flex gap-1">
                          <Button size="sm" variant="outline" className="h-7 px-2" disabled={controlMutation.isPending || !sub.running}
                            onClick={() => control(sub.control!, "stop", sub.name)}>
                            <Pause className="size-3" /> Стоп
                          </Button>
                          <Button size="sm" variant="outline" className="h-7 px-2" disabled={controlMutation.isPending || sub.running}
                            onClick={() => control(sub.control!, "start", sub.name)}>
                            <Play className="size-3" /> Пуск
                          </Button>
                        </div>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TabsContent>

          <TabsContent value="profiler" className="mt-0 space-y-4">
            <Card>
              <CardHeader className="py-3 pb-2">
                <CardTitle className="flex items-center gap-2 text-sm">
                  <Cpu className="size-4" /> CPU по функциям
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <Select value={profilerSeconds} onValueChange={setProfilerSeconds}>
                    <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="3">3 секунды</SelectItem>
                      <SelectItem value="6">6 секунд</SelectItem>
                      <SelectItem value="15">15 секунд</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button size="sm" disabled={cpuMutation.isPending}
                    onClick={() => cpuMutation.mutate()}>
                    {cpuMutation.isPending ? "Снимаю…" : `Снять срез (${profilerSeconds} с)`}
                  </Button>
                  <span className="text-xs text-muted-foreground">Сэмплирующий профайлер: пропорция сэмплов ≈ доля CPU</span>
                </div>
                {cpuMutation.data && (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Функция</TableHead>
                        <TableHead className="text-right">Доля</TableHead>
                        <TableHead className="text-right">Сэмплов</TableHead>
                        <TableHead>Файл</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {cpuMutation.data.top.map((r, i) => (
                        <TableRow key={i}>
                          <TableCell className="font-mono text-xs">{r.function}</TableCell>
                          <TableCell className={`text-right font-mono text-sm ${r.pct > 15 ? "text-warning" : ""}`}>{r.pct}%</TableCell>
                          <TableCell className="text-right font-mono text-xs text-muted-foreground">{r.samples}</TableCell>
                          <TableCell className="font-mono text-xs text-muted-foreground">{r.file}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="py-3 pb-2">
                <CardTitle className="flex items-center gap-2 text-sm">
                  <MemoryStick className="size-4" /> RAM по функциям (tracemalloc)
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <Button size="sm" variant="outline" disabled={memMutation.isPending}
                    onClick={() => memMutation.mutate("start")}>Включить</Button>
                  <Button size="sm" disabled={memMutation.isPending || !memMutation.data?.status}
                    onClick={() => memMutation.mutate("snapshot")}>Снимок</Button>
                  <Button size="sm" variant="outline" disabled={memMutation.isPending || !memMutation.data?.status}
                    onClick={() => memMutation.mutate("stop")}>Выключить</Button>
                  <span className="text-xs text-muted-foreground">При включении бэкенд начинает отслеживать аллокации (~10-15% накладных расходов), снимок показывает: лидеров текущей памяти и прирост с прошлого снимка.</span>
                </div>
                {memMutation.data && ((memMutation.data.top?.length ?? 0) > 0 || (memMutation.data.growth?.length ?? 0) > 0) ? (
                  <div className="grid gap-4 xl:grid-cols-2">
                    <div>
                      <p className="mb-1 text-xs font-semibold text-muted-foreground">Топ уровней памяти сейчас</p>
                      <Table>
                        <TableHeader>
                          <TableRow><TableHead>Место</TableHead><TableHead className="text-right">МБ</TableHead><TableHead className="text-right">Объектов</TableHead></TableRow>
                        </TableHeader>
                        <TableBody>
                          {(memMutation.data.top ?? []).slice(0, 20).map((r, i) => (
                            <TableRow key={i}>
                              <TableCell className="font-mono text-xs">{r.location}</TableCell>
                              <TableCell className="text-right font-mono text-sm">{r.size_mb}</TableCell>
                              <TableCell className="text-right font-mono text-xs text-muted-foreground">{r.count}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                    <div>
                      <p className="mb-1 text-xs font-semibold text-muted-foreground">Прирост с прошлого снимка</p>
                      <Table>
                        <TableHeader>
                          <TableRow><TableHead>Место</TableHead><TableHead className="text-right">Δ МБ</TableHead></TableRow>
                        </TableHeader>
                        <TableBody>
                          {(memMutation.data.growth ?? []).slice(0, 20).map((r, i) => (
                            <TableRow key={i} className={r.delta_mb > 0.5 ? "text-warning" : ""}>
                              <TableCell className="font-mono text-xs">{r.location}</TableCell>
                              <TableCell className="text-right font-mono text-sm">{r.delta_mb > 0 ? "+" : ""}{r.delta_mb}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                ) : memMutation.data ? (
                  <div className="rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">Данные снимка пусты</div>
                ) : null}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </CardContent>

      <ConfirmDialog
        open={!!pending}
        onOpenChange={(o) => { if (!o) setPending(null) }}
        title={pending?.title ?? ""}
        description={pending?.description ?? ""}
        confirmLabel="Выполнить"
        onConfirm={act}
      />
    </Card>
  )
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

  // Диспетчер задач: живой опрос каждые ~2с (первый ответ после старта
  // бэкенда покажет 0% — окно дельты ещё не набралось).
  const { data: taskManager } = useQuery({
    queryKey: ["perf-task-manager"],
    queryFn: () => api.get<TaskManagerResponse>("/api/v1/admin/perf/task-manager"),
    enabled,
    refetchInterval: 2200,
    refetchIntervalInBackground: false,
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

  const [tmTab, setTmTab] = useState("processes")

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
        <Card data-v2-chart className="mb-4 overflow-hidden py-0">
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

      {taskManager && <TaskManagerSection data={taskManager} tab={tmTab} onTab={setTmTab} onRefetch={() => queryClient.invalidateQueries({ queryKey: ["perf-task-manager"] })} />}

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
