import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { RefreshCw, Play, Pause, RotateCw, OctagonX, Copy } from "lucide-react"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"

type Worker = { id: number; name: string; enabled: boolean; status: string; current_job_id: number | null; capabilities: string[]; version: string | null; last_seen_at: string | null }
type Job = { id: number; task_type: string; status: string; worker: string | null; attempts: number; created_at: string | null; error: string | null }
type WorkerData = { nodes: Worker[]; jobs: Job[]; logs: { id: number; worker: string | null; job_id: number | null; level: string; message: string; created_at: string | null }[] }

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
  const copySetup = async () => {
    if (!setup) return
    const value = `NEXUSDATA_URL=${window.location.origin}\nNEXUSDATA_WORKER_TOKEN=${setup.token}\nNEXUSDATA_WORKER_NAME=${setup.name}\nNEXUSDATA_WORKER_DATA=./worker-data`
    await navigator.clipboard.writeText(value)
    toast.success("Настройки скопированы")
  }

  return <Card className="overflow-hidden py-0">
    <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="flex items-center justify-between text-sm font-semibold">Внешние воркеры <Button size="sm" variant="ghost" onClick={() => query.refetch()}><RefreshCw className="mr-1 size-3.5" />Обновить</Button></CardTitle></CardHeader>
    <CardContent className="space-y-5 py-4">
      <p className="text-sm text-muted-foreground">Воркеры подключаются к NexusData по исходящему HTTPS-соединению. Доступа к БД и файловому хранилищу у них нет. Задания и журнал сохраняются в NexusData; незавершённые задания повторяются после истечения аренды.</p>
      <p className="text-xs text-muted-foreground">Для голосовых задач установите на машине Python 3.12, .NET 8 и путь <code>VOICE_PROFILE_DECODER_PATH</code>. Модель ONNX воркер получает от NexusData только на время назначенного задания.</p>
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-56 flex-1"><Label className="text-xs">Имя нового воркера</Label><Input className="mt-1" value={name} onChange={(e) => setName(e.target.value)} /></div>
        <Button onClick={() => register.mutate()} disabled={register.isPending || !name.trim()}>Зарегистрировать и получить настройки</Button>
      </div>
      {setup && <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
        <div className="flex items-center justify-between gap-2"><div className="text-sm font-medium">Настройки подключения для {setup.name}</div><Button size="sm" variant="outline" onClick={copySetup}><Copy className="mr-1 size-3.5" />Скопировать env</Button></div>
        <p className="break-all font-mono text-xs text-muted-foreground">NEXUSDATA_URL={window.location.origin}<br />NEXUSDATA_WORKER_TOKEN={setup.token}<br />NEXUSDATA_WORKER_NAME={setup.name}<br />NEXUSDATA_WORKER_DATA=./worker-data</p>
        <p className="text-xs text-warning">Токен показывается только сейчас. Поместите его в .env на машине воркера.</p>
      </div>}
      <div className="space-y-2">
        <h3 className="text-sm font-medium">Подключённые машины</h3>
        {!data?.nodes.length && <p className="text-sm text-muted-foreground">Воркеры ещё не зарегистрированы.</p>}
        {data?.nodes.map((worker) => <div key={worker.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3">
          <div className="min-w-48"><div className="flex items-center gap-2 font-medium">{worker.name}<Badge variant={worker.status === "busy" ? "default" : "outline"}>{worker.status}</Badge></div><div className="mt-1 text-xs text-muted-foreground">{worker.capabilities.join(", ") || "Модули не объявлены"} · {worker.version ?? "версия неизвестна"} · {worker.last_seen_at ? new Date(worker.last_seen_at).toLocaleString() : "ещё не подключался"}</div>{worker.current_job_id && <div className="mt-1 text-xs">Выполняет задание #{worker.current_job_id}</div>}</div>
          <div className="flex flex-wrap gap-1"><Button size="sm" variant="outline" onClick={() => command.mutate({ id: worker.id, command: "restart" })}><RotateCw className="mr-1 size-3.5" />Перезапустить</Button><Button size="sm" variant="outline" onClick={() => command.mutate({ id: worker.id, command: worker.enabled ? "stop" : "resume" })}>{worker.enabled ? <><Pause className="mr-1 size-3.5" />Остановить</> : <><Play className="mr-1 size-3.5" />Запустить</>}</Button><Button size="sm" variant="destructive" disabled={!worker.current_job_id} onClick={() => command.mutate({ id: worker.id, command: "stop_task" })}><OctagonX className="mr-1 size-3.5" />Остановить задачу</Button><Button size="sm" variant="outline" onClick={() => rotateToken.mutate(worker.id)} disabled={rotateToken.isPending}>Новый токен</Button></div>
        </div>)}
      </div>
      <div className="space-y-2"><h3 className="text-sm font-medium">Последние задания</h3><div className="max-h-56 space-y-1 overflow-auto">{data?.jobs.map((job) => <div key={job.id} className="flex flex-wrap items-center justify-between gap-2 border-b py-1.5 text-xs"><span>#{job.id} · {job.task_type} · {job.worker ?? "ожидает"}</span><span className="flex items-center gap-2 text-muted-foreground">{job.status} · попыток: {job.attempts}{job.error ? ` · ${job.error}` : ""}{["failed", "cancelled"].includes(job.status) && <Button size="sm" variant="outline" onClick={() => retry.mutate(job.id)} disabled={retry.isPending}>Повторить</Button>}</span></div>)}</div></div>
      <div className="space-y-2"><h3 className="text-sm font-medium">Журнал</h3><div className="max-h-64 space-y-1 overflow-auto rounded-md bg-black/5 p-2 font-mono text-xs dark:bg-white/5">{data?.logs.map((line) => <div key={line.id} className="break-words"><span className="text-muted-foreground">{line.created_at ? new Date(line.created_at).toLocaleString() : ""} [{line.worker ?? "worker"}{line.job_id ? ` #${line.job_id}` : ""}]</span> {line.message}</div>)}</div></div>
    </CardContent>
  </Card>
}
