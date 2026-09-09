import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { History, Loader2, Play, ShieldAlert, Volume2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { useCurrentAdmin } from "@/lib/auth"

interface VoiceRun {
  id: number
  status: "pending" | "running" | "completed" | "failed"
  total: number
  processed: number
  ready_count: number
  error_count: number
  last_replay_id: number | null
  last_message: string | null
  error: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  heartbeat_at: string | null
}

interface VoiceStatus {
  active: VoiceRun | null
  latest: VoiceRun | null
  log_retention_days: number
  index_version: number
}

interface VoiceLogResponse {
  items: Array<{
    id: number
    replay_id: number | null
    level: string
    status: string
    message: string
    created_at: string | null
  }>
  total: number
  page: number
  pages: number
  run: VoiceRun
  retention_days: number
}

function statusLabel(status: VoiceRun["status"]): string {
  return ({ pending: "В очереди", running: "Выполняется", completed: "Завершена", failed: "Ошибка" })[status]
}

function statusVariant(status: VoiceRun["status"]): "default" | "secondary" | "destructive" | "outline" {
  if (status === "failed") return "destructive"
  if (status === "completed") return "default"
  if (status === "running") return "secondary"
  return "outline"
}

export function VoiceIndexPage() {
  const { data: admin } = useCurrentAdmin()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)

  const statusQuery = useQuery({
    queryKey: ["voice-index-status"],
    queryFn: () => api.get<VoiceStatus>("/api/v1/admin/voice-index/status"),
    enabled: !!admin?.is_superuser,
    refetchInterval: (query) => query.state.data?.active ? 3000 : 15000,
  })
  const run = statusQuery.data?.active ?? statusQuery.data?.latest

  const logsQuery = useQuery({
    queryKey: ["voice-index-logs", run?.id, page],
    queryFn: () => api.get<VoiceLogResponse>(`/api/v1/admin/voice-index/runs/${run?.id}/logs?page=${page}&page_size=50`),
    enabled: !!admin?.is_superuser && !!run?.id,
    refetchInterval: statusQuery.data?.active ? 3000 : false,
  })

  const startMutation = useMutation({
    mutationFn: () => api.post<VoiceRun>("/api/v1/admin/voice-index/runs"),
    onSuccess: () => {
      setPage(1)
      queryClient.invalidateQueries({ queryKey: ["voice-index-status"] })
      toast.success("Индексация поставлена в очередь")
    },
    onError: (error: Error) => toast.error(error.message),
  })

  if (admin && !admin.is_superuser) {
    return (
      <Card><CardContent className="flex flex-col items-center gap-2 py-12 text-center text-muted-foreground">
        <ShieldAlert className="size-8" /> Голосовой архив доступен только суперпользователям.
      </CardContent></Card>
    )
  }

  const progress = run && run.total ? Math.round((run.processed / run.total) * 100) : 0

  return (
    <div>
      <PageHeader
        title="Голос"
        description="Индекс голосовых фрагментов из сохранённых replay. Аудио остаётся внутри .barodemo."
        icon={Volume2}
        actions={<Button onClick={() => startMutation.mutate()} disabled={!!statusQuery.data?.active || startMutation.isPending}>
          {startMutation.isPending ? <Loader2 className="animate-spin" /> : <Play />}
          {statusQuery.data?.active ? "Индексация идёт" : "Запустить индексацию"}
        </Button>}
      />

      <Card className="mb-4">
        <CardContent className="grid gap-4 p-4 md:grid-cols-4">
          <div><div className="text-xs text-muted-foreground">Состояние</div><div className="mt-1"><Badge variant={run ? statusVariant(run.status) : "outline"}>{run ? statusLabel(run.status) : "Не запускалась"}</Badge></div></div>
          <div><div className="text-xs text-muted-foreground">Прогресс</div><div className="mt-1 text-lg font-semibold">{run ? `${run.processed} / ${run.total}` : "—"}{run?.total ? <span className="ml-2 text-sm font-normal text-muted-foreground">({progress}%)</span> : null}</div></div>
          <div><div className="text-xs text-muted-foreground">Готово / ошибок</div><div className="mt-1 text-lg font-semibold">{run ? `${run.ready_count} / ${run.error_count}` : "—"}</div></div>
          <div><div className="text-xs text-muted-foreground">Последнее сообщение</div><div className="mt-1 truncate text-sm" title={run?.last_message ?? undefined}>{run?.last_message ?? "—"}</div></div>
        </CardContent>
      </Card>

      <Card className="overflow-hidden py-0">
        <div className="flex items-center gap-2 border-b px-4 py-3 font-medium"><History className="size-4" /> Журнал запуска <span className="text-xs font-normal text-muted-foreground">(хранится {statusQuery.data?.log_retention_days ?? 7} дней)</span></div>
        <CardContent className="p-0">
          {logsQuery.isLoading ? <div className="space-y-2 p-4">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8" />)}</div> : logsQuery.data?.items.length ? logsQuery.data.items.map((item) => (
            <div key={item.id} className="flex items-start gap-3 border-b px-4 py-2.5 text-sm last:border-0">
              <Badge variant={item.level === "error" ? "destructive" : "outline"}>{item.status}</Badge>
              <div className="min-w-0 flex-1"><div>{item.message}</div>{item.replay_id && <div className="text-xs text-muted-foreground">replay #{item.replay_id}</div>}</div>
              <span className="shrink-0 text-xs text-muted-foreground">{formatBrowserDate(item.created_at)}</span>
            </div>
          )) : <div className="py-10 text-center text-sm text-muted-foreground">Журнал пуст. Пока даже ошибки не успели организоваться.</div>}
          {logsQuery.data && logsQuery.data.pages > 1 && <div className="flex items-center justify-center gap-2 border-t p-3"><Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Назад</Button><span className="text-sm text-muted-foreground">Страница {page} из {logsQuery.data.pages}</span><Button variant="outline" size="sm" disabled={page >= logsQuery.data.pages} onClick={() => setPage((value) => value + 1)}>Вперёд</Button></div>}
        </CardContent>
      </Card>
    </div>
  )
}
