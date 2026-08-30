/** Журнал серверных ошибок — то, что ERROR/CRITICAL-логгеры пишут по всему
 * сайту/API (см. services_error_log.DatabaseErrorHandler), а не только
 * необработанные исключения. Доступ жёстко — только суперпользователям
 * (routes/error_log.py гейтит is_superuser, не через permissions_registry:
 * трассы исключений могут содержать чувствительные детали).
 */
import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Bug, ChevronDown, ChevronRight, ShieldAlert, Trash2 } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"

interface ErrorLogEntryRow {
  id: number
  level: string
  logger_name: string | null
  message: string
  traceback: string | null
  path: string | null
  method: string | null
  created_at: string | null
}

interface ErrorLogResponse {
  entries: ErrorLogEntryRow[]
  total: number
  page: number
  pages: number
  retention_days: number
}

function fmtDate(iso: string | null): string {
  if (!iso) return "-"
  const hasTz = iso.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(iso)
  const normalized = hasTz ? iso : iso + "Z"
  return new Date(normalized).toLocaleString("ru-RU")
}

function EntryRow({ entry }: { entry: ErrorLogEntryRow }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="border-b last:border-0">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-start gap-2 px-3 py-2.5 text-left hover:bg-accent/50"
      >
        {expanded ? <ChevronDown className="mt-0.5 size-4 shrink-0 text-muted-foreground" /> : <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
        <Badge variant={entry.level === "CRITICAL" ? "destructive" : "outline"} className={entry.level === "CRITICAL" ? "" : "border-destructive/40 text-destructive"}>
          {entry.level}
        </Badge>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm">{entry.message}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            {entry.logger_name && <span className="font-mono">{entry.logger_name}</span>}
            {entry.path && <span className="font-mono">{entry.method ?? ""} {entry.path}</span>}
          </div>
        </div>
        <span className="shrink-0 text-xs whitespace-nowrap text-muted-foreground">{fmtDate(entry.created_at)}</span>
      </button>
      {expanded && (
        <div className="border-t bg-muted/30 px-3 py-2">
          {entry.traceback ? (
            <pre className="max-h-96 overflow-auto rounded-md bg-background p-2 font-mono text-[11px] whitespace-pre-wrap">{entry.traceback}</pre>
          ) : (
            <p className="text-xs text-muted-foreground">Трассы нет — лог без исключения (logger.error без exc_info).</p>
          )}
        </div>
      )}
    </div>
  )
}

export function ErrorLogPage() {
  const { data: admin } = useCurrentAdmin()
  const [level, setLevel] = useState("all")
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(1)
  const [clearOpen, setClearOpen] = useState(false)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ["error-log", level, search, page],
    queryFn: () => api.get<ErrorLogResponse>(`/api/v1/admin/error-log?${new URLSearchParams({
      level: level === "all" ? "" : level, search, page: String(page),
    })}`),
    enabled: !!admin?.is_superuser,
  })

  const clearMutation = useMutation({
    mutationFn: () => api.delete<{ deleted: number }>("/api/v1/admin/error-log"),
    onSuccess: (res) => {
      toast.success(`Журнал очищен (${res.deleted})`)
      setClearOpen(false)
      queryClient.invalidateQueries({ queryKey: ["error-log"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (admin && !admin.is_superuser) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-2 py-12 text-center text-muted-foreground">
          <ShieldAlert className="size-8" />
          Журнал ошибок доступен только суперпользователям.
        </CardContent>
      </Card>
    )
  }

  return (
    <div>
      <PageHeader
        title="Журнал ошибок"
        description="ERROR/CRITICAL-логи сайта и API — только для суперпользователей"
        icon={Bug}
        actions={data && (
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="text-sm font-normal">{data.total} записей · хранится {data.retention_days} дн.</Badge>
            <Button size="sm" variant="destructive" onClick={() => setClearOpen(true)}>
              <Trash2 className="size-3.5" /> Очистить журнал
            </Button>
          </div>
        )}
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <SearchInput
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1) }}
          placeholder="Поиск по сообщению, трассе, логгеру или пути запроса..."
          className="max-w-sm"
        />
        <Select value={level} onValueChange={(v) => { setLevel(v); setPage(1) }}>
          <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все уровни</SelectItem>
            <SelectItem value="ERROR">ERROR</SelectItem>
            <SelectItem value="CRITICAL">CRITICAL</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <Card className="overflow-hidden py-0">
        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-4">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div>
          ) : (
            <>
              {data?.entries.map((e) => <EntryRow key={e.id} entry={e} />)}
              {!data?.entries.length && (
                <div className="py-10 text-center text-muted-foreground">
                  {search || level !== "all" ? "Ничего не найдено по этим фильтрам" : "Ошибок пока не было — хорошая новость"}
                </div>
              )}
            </>
          )}
          {data && data.pages > 1 && (
            <div className="flex items-center justify-center gap-2 border-t p-3">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Назад</Button>
              <span className="text-sm text-muted-foreground">Страница {data.page} из {data.pages}</span>
              <Button variant="outline" size="sm" disabled={page >= data.pages} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
            </div>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={clearOpen}
        onOpenChange={setClearOpen}
        title="Очистить журнал ошибок?"
        description="Удалит все записи безвозвратно. Новые ошибки будут писаться заново."
        confirmLabel="Очистить"
        onConfirm={() => clearMutation.mutate()}
      />
    </div>
  )
}
