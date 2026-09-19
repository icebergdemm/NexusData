import { useEffect } from "react"
import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { useSearchParams } from "react-router-dom"
import { CalendarClock, RotateCcw, Search, ShieldCheck } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { api } from "@/lib/api"
import { AuditEventDetails } from "./AuditEventDetails"
import { AuditSessionRail } from "./AuditSessionRail"
import { AuditTimeline } from "./AuditTimeline"
import type { AuditLog, AuditResponse, AuditSession, AuditSessionsResponse } from "./audit-types"

function datetimeLocalValue(value: string): string {
  if (!value) return ""
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return value
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  const pad = (part: number) => String(part).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function AuditPage({ embedded = false }: { embedded?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const adminId = Number(searchParams.get("admin") ?? searchParams.get("admin_id"))
  const hasAdmin = Number.isInteger(adminId) && adminId > 0
  const requestedSession = Number(searchParams.get("session"))
  const hasRequestedSession = Number.isInteger(requestedSession) && requestedSession > 0
  const eventId = Number(searchParams.get("event"))
  const hasEvent = Number.isInteger(eventId) && eventId > 0
  const source = searchParams.get("source") || "all"
  const q = searchParams.get("q") || ""
  const action = searchParams.get("action") || ""
  const category = searchParams.get("category") || "all"
  const from = searchParams.get("from") || searchParams.get("date_from") || ""
  const to = searchParams.get("to") || searchParams.get("date_to") || ""
  const showViews = searchParams.get("views") === "1"
  const debouncedQ = useDebouncedValue(q.trim(), 350)
  const debouncedAction = useDebouncedValue(action.trim(), 350)

  const updateParam = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next)
  }

  const sessionsQuery = useQuery({
    queryKey: ["audit-sessions", adminId, hasAdmin, debouncedQ],
    queryFn: () => {
      const params = new URLSearchParams({ page: "1", page_size: "80" })
      if (hasAdmin) params.set("admin_id", String(adminId))
      if (debouncedQ) params.set("q", debouncedQ)
      return api.get<AuditSessionsResponse>(`/api/v1/admin/audit/sessions?${params.toString()}`)
    },
  })
  const sessions = sessionsQuery.data?.sessions ?? []
  const selectedSessionId = hasRequestedSession ? requestedSession : (sessions.find((item) => item.session_id !== null)?.session_id ?? null)
  const selectedSession = sessions.find((item) => item.session_id === selectedSessionId) ?? sessions[0] ?? null

  useEffect(() => {
    if (!hasRequestedSession && selectedSession?.session_id != null) {
      const next = new URLSearchParams(searchParams)
      next.set("session", String(selectedSession.session_id))
      setSearchParams(next, { replace: true })
    }
  }, [hasRequestedSession, selectedSession?.session_id, searchParams, setSearchParams])

  const logsQuery = useInfiniteQuery({
    queryKey: ["audit-workspace-logs", source, adminId, selectedSessionId, debouncedQ, category, debouncedAction, from, to, showViews],
    queryFn: ({ pageParam = 1 }) => {
      const params = new URLSearchParams({ page: String(pageParam as number), page_size: "50", source })
      if (hasAdmin) params.set("admin", String(adminId))
      if (selectedSessionId != null) params.set("session_id", String(selectedSessionId))
      if (debouncedQ) params.set("q", debouncedQ)
      if (category !== "all") params.set("category", category)
      if (debouncedAction) params.set("action", debouncedAction)
      if (from) params.set("from", new Date(from).toISOString())
      if (to) params.set("to", new Date(to).toISOString())
      if (!showViews) params.set("exclude_views", "true")
      return api.get<AuditResponse>(`/api/v1/admin/audit/logs?${params.toString()}`)
    },
    initialPageParam: 1,
    getNextPageParam: (lastPage: AuditResponse) =>
      lastPage.page < lastPage.total_pages ? lastPage.page + 1 : undefined,
  })
  const logs = logsQuery.data?.pages.flatMap((page) => page.logs) ?? []
  const total = logsQuery.data?.pages[0]?.total ?? 0
  const selectedLoadedLog = logs.find((log) => log.id === eventId) ?? null
  const selectedRemoteLog = useQuery({
    queryKey: ["audit-log", eventId],
    queryFn: () => api.get<AuditLog>(`/api/v1/admin/audit/logs/${eventId}`),
    enabled: hasEvent && !selectedLoadedLog,
  })
  const selectedLog = selectedRemoteLog.data ?? selectedLoadedLog
  const lastPage = logsQuery.data?.pages.at(-1)
  const hasNextPage = Boolean(lastPage && lastPage.page < lastPage.total_pages)
  const hasFilters = hasAdmin || hasRequestedSession || Boolean(debouncedQ || debouncedAction || from || to || category !== "all" || source !== "all" || showViews)

  const selectSession = (session: AuditSession) => {
    const next = new URLSearchParams(searchParams)
    next.delete("event")
    if (session.session_id == null) next.delete("session")
    else next.set("session", String(session.session_id))
    setSearchParams(next)
  }
  const selectEvent = (log: AuditLog) => updateParam("event", String(log.id))

  return (
    <div className="space-y-4">
      {!embedded && <PageHeader title="Аудит" description="Цепочки доступа и действия администраторов" icon={ShieldCheck} actions={<Badge variant="secondary">{total} записей всего</Badge>} />}
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
        <div><span className="font-medium text-foreground">Рабочее место расследования</span><span className="ml-2">{total} событий в выборке</span></div>
        <label className="inline-flex items-center gap-2 text-xs"><input type="checkbox" checked={showViews} onChange={(event) => updateParam("views", event.target.checked ? "1" : "")} /> показывать чувствительные просмотры</label>
      </div>
      <div className="rounded-lg border bg-card/30 p-3 sm:p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1"><Label className="mb-1.5 block text-xs">Поиск по журналу</Label><SearchInput value={q} onChange={(event) => updateParam("q", event.target.value)} placeholder="Автор, Discord ID, действие или детали" /></div>
          <div className="w-40"><Label className="mb-1.5 block text-xs">Категория</Label><Select value={category} onValueChange={(value) => updateParam("category", value === "all" ? "" : value)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все категории</SelectItem><SelectItem value="create">Создание</SelectItem><SelectItem value="update">Изменение</SelectItem><SelectItem value="delete">Удаление</SelectItem><SelectItem value="auth">Авторизация</SelectItem><SelectItem value="other">Прочее</SelectItem></SelectContent></Select></div>
          <div className="w-48"><Label className="mb-1.5 block text-xs">Действие</Label><div className="relative"><Search className="pointer-events-none absolute left-2 top-2.5 size-3.5 text-muted-foreground" /><Input value={action} onChange={(event) => updateParam("action", event.target.value)} placeholder="ban, create…" className="pl-7" /></div></div>
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div className="flex gap-2"><Button size="sm" variant={source === "panel" ? "default" : "outline"} onClick={() => updateParam("source", "panel")}>Панель</Button><Button size="sm" variant={source === "discord" ? "default" : "outline"} onClick={() => updateParam("source", "discord")}>Discord</Button><Button size="sm" variant={source === "all" ? "default" : "outline"} onClick={() => updateParam("source", "all")}>Все</Button></div>
          <div><Label className="mb-1.5 flex items-center gap-1 text-xs"><CalendarClock className="size-3" /> С даты</Label><Input type="datetime-local" value={datetimeLocalValue(from)} onChange={(event) => updateParam("from", event.target.value)} className="w-44" /></div>
          <div><Label className="mb-1.5 block text-xs">По дату</Label><Input type="datetime-local" value={datetimeLocalValue(to)} onChange={(event) => updateParam("to", event.target.value)} className="w-44" /></div>
          {hasFilters && <Button size="sm" variant="ghost" onClick={() => setSearchParams({})}><RotateCcw className="size-3.5" /> Сбросить</Button>}
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border bg-card/20 xl:grid xl:grid-cols-[240px_minmax(420px,1fr)_320px]">
        {sessionsQuery.isLoading ? <div className="p-4"><Skeleton className="h-40" /></div> : <AuditSessionRail sessions={sessions} selectedSessionId={selectedSessionId} onSelect={selectSession} />}
        <main className="min-w-0 border-b border-border xl:border-b-0">
          <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3 sm:px-5"><div><h2 className="font-semibold">Цепочка действий</h2><p className="mt-0.5 text-xs text-muted-foreground">{selectedSession ? `${selectedSession.admin_display_name || selectedSession.admin_name || "Актор"} · ${selectedSession.auth_type || "источник не указан"}` : "Все события"}</p></div>{selectedSession?.status === "active" && <Badge variant="default">активна</Badge>}</div>
          {logsQuery.isLoading ? <div className="space-y-2 p-5">{Array.from({ length: 6 }).map((_, index) => <Skeleton key={index} className="h-16" />)}</div> : <AuditTimeline logs={logs} selectedEventId={hasEvent ? eventId : null} onSelect={selectEvent} />}
          {hasNextPage && <div className="border-t border-border p-3 text-center"><Button size="sm" variant="ghost" onClick={() => logsQuery.fetchNextPage()} disabled={logsQuery.isFetchingNextPage}>{logsQuery.isFetchingNextPage ? "Загрузка…" : "Загрузить ещё"}</Button></div>}
          {!hasNextPage && logs.length > 0 && <div className="border-t border-border px-4 py-2 text-center text-[10px] text-muted-foreground">Загружено {logs.length} из {total}</div>}
        </main>
        <AuditEventDetails log={selectedLog} />
      </div>
    </div>
  )
}
