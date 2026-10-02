import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Activity, AlertTriangle, CheckCircle2, Clock3, Copy, Cpu, Pause, Play, RefreshCw,
  RotateCw, Server, XCircle,
} from "lucide-react"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"

type Worker = { id: number; name: string; enabled: boolean; status: string; current_job_id: number | null; capabilities: string[]; version: string | null; last_seen_at: string | null }
type Job = { id: number; task_type: string; status: string; worker: string | null; attempts: number; created_at: string | null; error: string | null }
type WorkerData = { nodes: Worker[]; jobs: Job[]; logs: { id: number; worker: string | null; job_id: number | null; level: string; message: string; created_at: string | null }[] }

const workerState: Record<string, { label: string; tone: string; icon: typeof Activity }> = {
  busy: { label: "Выполняет задачу", tone: "border-info/40 bg-info/10 text-info", icon: Activity },
  idle: { label: "Готов к работе", tone: "border-success/40 bg-success/10 text-success", icon: CheckCircle2 },
  online: { label: "Подключён", tone: "border-success/40 bg-success/10 text-success", icon: CheckCircle2 },
  paused: { label: "Приостановлен", tone: "border-warning/40 bg-warning/10 text-warning", icon: Pause },
  stopping: { label: "Останавливается", tone: "border-warning/40 bg-warning/10 text-warning", icon: Clock3 },
  disabled: { label: "Отключён", tone: "border-border bg-muted text-muted-foreground", icon: Pause },
  offline: { label: "Нет связи", tone: "border-destructive/40 bg-destructive/10 text-destructive", icon: XCircle },
}

const jobState: Record<string, { label: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
  queued: { label: "В очереди", variant: "secondary" },
  running: { label: "В работе", variant: "default" },
  completed: { label: "Готово", variant: "outline" },
  failed: { label: "Ошибка", variant: "destructive" },
  cancelled: { label: "Отменено", variant: "outline" },
}

const taskLabel: Record<string, string> = {
  "voice_profile.extract": "Извлечение голосового профиля",
  "voice_profile.voiceprint": "Расчёт голосового отпечатка",
}

const capabilityLabel: Record<string, string> = {
  "voice_profile.extract": "Голосовые профили",
  "voice_profile.voiceprint": "Голосовые отпечатки",
}

function WorkerStatus({ status }: { status: string }) {
  const state = workerState[status] ?? { label: status || "Статус неизвестен", tone: "border-border bg-muted text-muted-foreground", icon: Server }
  const Icon = state.icon
  return <Badge variant="outline" className={`gap-1.5 ${state.tone}`}><Icon />{state.label}</Badge>
}

function JobStatus({ status }: { status: string }) {
  const state = jobState[status] ?? { label: status || "Неизвестно", variant: "outline" as const }
  return <Badge variant={state.variant}>{state.label}</Badge>
}

function formatDate(value: string | null) {
  if (!value) return "Время неизвестно"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? "Время неизвестно" : date.toLocaleString()
}

export function ExternalWorkersPanel() {
  const [name, setName] = useState("voice-worker-1")
  const [setup, setSetup] = useState<{ token: string; name: string } | null>(null)
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ["external-workers"], queryFn: () => api.get<WorkerData>("/api/v1/admin/external-workers"), refetchInterval: 5000 })
  const register = useMutation({
    mutationFn: () => api.post<{ token: string; name: string }>("/api/v1/admin/external-workers/register", { name }),
    onSuccess: (result) => { setSetup(result); toast.success("Воркер зарегистрирован. Сохраните токен: повторно он не показывается."); queryClient.invalidateQueries({ queryKey: ["external-workers"] }) },
    onError: (e: Error) => toast.error(e.message),
  })
  const command = useMutation({
    mutationFn: ({ id, command }: { id: number; command: string }) => api.post(`/api/v1/admin/external-workers/${id}/command`, { command }),
    onSuccess: () => { toast.success("Команда отправлена"); queryClient.invalidateQueries({ queryKey: ["external-workers"] }) },
    onError: (e: Error) => toast.error(e.message),
  })
  const retry = useMutation({
    mutationFn: (jobId: number) => api.post(`/api/v1/admin/external-workers/jobs/${jobId}/retry`),
    onSuccess: () => { toast.success("Задание возвращено в очередь"); queryClient.invalidateQueries({ queryKey: ["external-workers"] }) },
    onError: (e: Error) => toast.error(e.message),
  })
  const rotateToken = useMutation({
    mutationFn: (workerId: number) => api.post<{ name: string; token: string }>(`/api/v1/admin/external-workers/${workerId}/rotate-token`),
    onSuccess: (result) => { setSetup(result); toast.success("Новый токен готов. Обновите секрет в конфигурации воркера."); queryClient.invalidateQueries({ queryKey: ["external-workers"] }) },
    onError: (e: Error) => toast.error(e.message),
  })
  const data = query.data
  const workers = data?.nodes ?? []
  const jobs = data?.jobs ?? []
  const busyWorkers = workers.filter((worker) => worker.status === "busy").length
  const onlineWorkers = workers.filter((worker) => ["busy", "idle", "online"].includes(worker.status)).length
  const queuedJobs = jobs.filter((job) => job.status === "queued").length
  const failedJobs = jobs.filter((job) => job.status === "failed").length
  const copySetup = async () => {
    if (!setup) return
    const value = `NEXUSDATA_URL=${window.location.origin}\nNEXUSDATA_WORKER_TOKEN=${setup.token}\nNEXUSDATA_WORKER_NAME=${setup.name}\nNEXUSDATA_WORKER_DATA=./worker-data`
    await navigator.clipboard.writeText(value)
    toast.success("Настройки скопированы")
  }

  return <Card className="overflow-hidden py-0">
    <CardHeader className="border-b bg-muted/30 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2"><div className="flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary"><Cpu className="size-4" /></div><div><CardTitle className="text-sm font-semibold">Внешние воркеры</CardTitle><CardDescription className="text-xs">Подключение, очередь заданий и журнал</CardDescription></div></div>
        <div className="flex items-center gap-2"><span className="hidden text-xs text-muted-foreground sm:inline">{data ? `Обновлено ${formatDate(new Date(query.dataUpdatedAt).toISOString())}` : "Ожидание данных"}</span><Button size="sm" variant="outline" onClick={() => query.refetch()} disabled={query.isFetching}><RefreshCw className={`size-3.5 ${query.isFetching ? "animate-spin" : ""}`} />Обновить</Button></div>
      </div>
    </CardHeader>
    <CardContent className="space-y-5 py-4">
      <p className="text-sm text-muted-foreground">Воркеры подключаются к NexusData по исходящему HTTPS-соединению. Доступа к БД и файловому хранилищу у них нет. Задания и журнал сохраняются в NexusData; незавершённые задания повторяются после истечения аренды.</p>
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="flex items-center gap-3 rounded-xl border bg-card p-3"><div className="flex size-9 items-center justify-center rounded-lg bg-success/10 text-success"><Server className="size-4" /></div><div><div className="text-xl font-semibold leading-none">{onlineWorkers}<span className="text-sm font-normal text-muted-foreground"> / {workers.length}</span></div><div className="mt-1 text-xs text-muted-foreground">На связи</div></div></div>
        <div className="flex items-center gap-3 rounded-xl border bg-card p-3"><div className="flex size-9 items-center justify-center rounded-lg bg-info/10 text-info"><Activity className="size-4" /></div><div><div className="text-xl font-semibold leading-none">{busyWorkers}</div><div className="mt-1 text-xs text-muted-foreground">Выполняют задания</div></div></div>
        <div className={`flex items-center gap-3 rounded-xl border p-3 ${failedJobs ? "border-warning/40 bg-warning/5" : "bg-card"}`}><div className={`flex size-9 items-center justify-center rounded-lg ${failedJobs ? "bg-warning/10 text-warning" : "bg-muted text-muted-foreground"}`}>{failedJobs ? <AlertTriangle className="size-4" /> : <Clock3 className="size-4" />}</div><div><div className="text-xl font-semibold leading-none">{queuedJobs}<span className="text-sm font-normal text-muted-foreground"> в очереди</span>{failedJobs > 0 && <span className="ml-2 text-sm text-warning">{failedJobs} ошибок</span>}</div><div className="mt-1 text-xs text-muted-foreground">Среди последних {jobs.length} заданий</div></div></div>
      </div>
      <div className="rounded-xl border border-info/20 bg-info/5 p-3">
        <div className="flex items-start gap-2.5"><Activity className="mt-0.5 size-4 shrink-0 text-info" /><div className="space-y-1"><p className="text-sm font-medium">Обработка голоса</p><p className="text-xs leading-relaxed text-muted-foreground">Для голосовых задач используйте Python 3.12 и укажите <code>VOICE_PROFILE_DECODER_PATH</code> на self-contained Linux-декодер из worker bundle. .NET нужен только на машине сборки. Модель ONNX NexusData передаёт воркеру только на время назначенного задания.</p></div></div>
      </div>
      <div className="flex flex-wrap items-end gap-2 rounded-xl border bg-muted/10 p-3">
        <div className="min-w-56 flex-1"><Label className="text-xs">Имя нового воркера</Label><Input className="mt-1" value={name} onChange={(e) => setName(e.target.value)} /></div>
        <Button onClick={() => register.mutate()} disabled={register.isPending || !name.trim()}><Server className="size-4" />Зарегистрировать воркер</Button>
      </div>
      {setup && <div className="space-y-2 rounded-xl border border-warning/30 bg-warning/5 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><div className="text-sm font-medium">Настройки подключения для {setup.name}</div><Button size="sm" variant="outline" onClick={copySetup}><Copy className="size-3.5" />Скопировать env</Button></div>
        <p className="break-all font-mono text-xs text-muted-foreground">NEXUSDATA_URL={window.location.origin}<br />NEXUSDATA_WORKER_TOKEN={setup.token}<br />NEXUSDATA_WORKER_NAME={setup.name}<br />NEXUSDATA_WORKER_DATA=./worker-data</p>
        <p className="text-xs text-warning">Токен показывается только сейчас. Поместите его в .env на машине воркера.</p>
      </div>}
      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-semibold">Подключённые машины</h3><Badge variant="secondary">{workers.length}</Badge></div>
        {query.isLoading && <div className="rounded-xl border border-dashed p-5 text-center text-sm text-muted-foreground">Загружаем состояние воркеров…</div>}
        {query.isError && <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><XCircle className="size-4" />Не удалось загрузить состояние воркеров. Попробуйте обновить данные.</div>}
        {!query.isLoading && !query.isError && workers.length === 0 && <div className="rounded-xl border border-dashed p-5 text-center"><Server className="mx-auto size-5 text-muted-foreground" /><p className="mt-2 text-sm font-medium">Воркеры не зарегистрированы</p><p className="mt-1 text-xs text-muted-foreground">Зарегистрируйте машину выше, чтобы выдать ей настройки подключения.</p></div>}
        {workers.map((worker) => {
          const state = workerState[worker.status]
          const capabilities = worker.capabilities.map((capability) => capabilityLabel[capability] ?? capability)
          return <div key={worker.id} className={`space-y-3 rounded-xl border p-3 sm:p-4 ${worker.status === "offline" ? "border-destructive/25 bg-destructive/[0.02]" : worker.status === "busy" ? "border-info/25 bg-info/[0.02]" : "bg-card"}`}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-3"><div className={`mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg ${state?.tone ?? "bg-muted text-muted-foreground"}`}><Server className="size-4" /></div><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="font-semibold">{worker.name}</span><WorkerStatus status={worker.status} />{!worker.enabled && <Badge variant="outline">Отключён администратором</Badge>}</div><div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground"><span>Версия {worker.version ?? "неизвестна"}</span><span>Связь: {worker.last_seen_at ? formatDate(worker.last_seen_at) : "ещё не устанавливалась"}</span></div></div></div>
              <div className="flex flex-wrap gap-1.5">
                <Button size="sm" variant="outline" onClick={() => command.mutate({ id: worker.id, command: "restart" })} disabled={command.isPending}><RotateCw className="size-3.5" />Перезапустить</Button>
                <Button size="sm" variant="outline" onClick={() => command.mutate({ id: worker.id, command: worker.enabled ? "stop" : "resume" })} disabled={command.isPending}>{worker.enabled ? <><Pause className="size-3.5" />Остановить</> : <><Play className="size-3.5" />Запустить</>}</Button>
                <Button size="sm" variant="destructive" disabled={!worker.current_job_id || command.isPending} onClick={() => command.mutate({ id: worker.id, command: "stop_task" })}><XCircle className="size-3.5" />Остановить задачу</Button>
                <Button size="sm" variant="outline" onClick={() => rotateToken.mutate(worker.id)} disabled={rotateToken.isPending}>Новый токен</Button>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-1.5 border-t pt-2.5">
              <span className="mr-1 text-xs text-muted-foreground">Модули:</span>
              {capabilities.length ? capabilities.map((capability, index) => <Badge key={`${capability}-${index}`} variant="secondary" className="font-normal">{capability}</Badge>) : <span className="text-xs text-muted-foreground">Воркер ещё не объявил доступные модули</span>}
              {worker.current_job_id && <Badge variant="outline" className="ml-auto font-mono">Задание #{worker.current_job_id}</Badge>}
            </div>
          </div>
        })}
      </section>
      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-semibold">Последние задания</h3><span className="text-xs text-muted-foreground">До 30 записей</span></div>
        <div className="max-h-72 space-y-2 overflow-auto pr-1">
          {jobs.map((job) => <div key={job.id} className="flex flex-wrap items-start justify-between gap-3 rounded-lg border bg-card px-3 py-2.5">
            <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-x-2 gap-y-1"><span className="font-mono text-xs text-muted-foreground">#{job.id}</span><span className="text-sm font-medium">{taskLabel[job.task_type] ?? job.task_type}</span><JobStatus status={job.status} /></div><div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground"><span>{job.worker ? `Воркер: ${job.worker}` : "Ожидает назначения"}</span><span>{formatDate(job.created_at)}</span><span>Попыток: {job.attempts}</span></div>{job.error && <p className="mt-2 break-words rounded-md bg-destructive/5 px-2 py-1.5 text-xs text-destructive">{job.error}</p>}</div>
            {["failed", "cancelled"].includes(job.status) && <Button size="sm" variant="outline" onClick={() => retry.mutate(job.id)} disabled={retry.isPending}>Повторить</Button>}
          </div>)}
          {!jobs.length && <div className="rounded-xl border border-dashed p-5 text-center text-sm text-muted-foreground">Заданий пока нет.</div>}
        </div>
      </section>
      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-semibold">Журнал</h3><span className="text-xs text-muted-foreground">Последние 100 записей</span></div>
        <div className="max-h-64 space-y-1 overflow-auto rounded-xl border bg-muted/20 p-3 font-mono text-xs">{data?.logs.map((line) => <div key={line.id} className="break-words leading-relaxed"><span className="text-muted-foreground">{line.created_at ? formatDate(line.created_at) : ""} [{line.worker ?? "worker"}{line.job_id ? ` #${line.job_id}` : ""}]</span> {line.message}</div>)}{data && !data.logs.length && <div className="py-2 text-center text-muted-foreground">Записей пока нет.</div>}</div>
      </section>
    </CardContent>
  </Card>
}
