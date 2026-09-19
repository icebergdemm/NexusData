import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { FileJson } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { actorOf, actorName, actorSecondaryName, eventLabel, formatChangeValue } from "./audit-formatters"
import type { AuditLog } from "./audit-types"

export function AuditEventDetails({ log }: { log: AuditLog | null }) {
  const [showRaw, setShowRaw] = useState(false)
  const { data: rawLog, isFetching } = useQuery({
    queryKey: ["audit-log-raw", log?.id],
    queryFn: () => api.get<AuditLog>(`/api/v1/admin/audit/logs/${log?.id}?include_raw=true`),
    enabled: Boolean(log?.id && showRaw),
  })
  if (!log) return <aside className="hidden min-h-[520px] place-items-center border-l border-border bg-card p-6 text-center text-sm text-muted-foreground xl:grid">Выберите событие, чтобы увидеть контекст.</aside>
  const actor = actorOf(log)
  const secondary = actorSecondaryName(actor)
  const structured = log.structured_details
  const raw = rawLog?.structured_details?.raw
  return (
    <aside className="min-w-0 border-t border-border bg-card xl:border-l xl:border-t-0">
      <div className="border-b border-border px-4 py-3 sm:px-5">
        <div className="text-[10px] uppercase tracking-[0.12em] text-primary">Выбранное событие</div>
        <h2 className="mt-1 text-sm font-semibold">{eventLabel(log)}</h2>
        <Badge variant={log.outcome === "failure" ? "destructive" : "default"} className="mt-2">{log.outcome === "failure" ? "отклонено" : "успешно"}</Badge>
      </div>
      <div className="space-y-5 p-4 sm:p-5">
        <section><div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Автор</div>{log.admin_id ? <Link to={`/security/admins?admin=${log.admin_id}`} className="text-sm font-semibold text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{actorName(actor)}</Link> : <div className="text-sm font-semibold">{actorName(actor)}</div>}{secondary && <div className="mt-0.5 text-xs text-muted-foreground">NexusData: {secondary}</div>}{actor.discord_id && <div className="mt-1 font-mono text-[10px] text-muted-foreground">Discord ID: {actor.discord_id}</div>}</section>
        <section><div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Событие</div><div className="font-mono text-xs text-primary">{log.action}</div><div className="mt-1 text-xs text-muted-foreground">{log.created_at ? new Date(log.created_at).toLocaleString("ru-RU") : "время не указано"} · {structured?.context.source ?? "Панель"}</div></section>
        {structured?.target && <section><div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Объект</div><div className="text-sm">{structured.target.label || `${structured.target.type} #${structured.target.id}`}</div></section>}
        {structured?.changes?.length ? <section><div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Изменения</div><div className="space-y-2">{structured.changes.map((change) => <div key={change.field} className="grid grid-cols-[1fr_auto_1fr] gap-2 font-mono text-[11px]"><span className="truncate text-destructive">{formatChangeValue(change.from)}</span><span className="text-muted-foreground">→</span><span className="truncate text-success">{formatChangeValue(change.to)}</span><span className="col-span-3 text-[10px] text-muted-foreground">{change.field}</span></div>)}</div></section> : null}
        {structured?.reason && <section><div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Причина</div><p className="text-sm text-foreground/80">{structured.reason}</p></section>}
        {structured?.fields?.length ? <section><div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Детали</div><dl className="divide-y divide-border rounded-md border border-border">{structured.fields.map((field) => <div key={field.key} className="grid grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] gap-3 px-3 py-2 text-xs"><dt className="truncate text-muted-foreground">{field.key}</dt><dd className="break-words text-right text-foreground">{formatChangeValue(field.value)}</dd></div>)}</dl></section> : null}
        <section><div className="mb-2 text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Контекст запроса</div><dl className="space-y-1 text-xs text-muted-foreground"><div className="flex justify-between gap-3"><dt>Сессия</dt><dd className="font-mono text-foreground">{log.session_id ? `#${log.session_id}` : "не браузерная"}</dd></div><div className="flex justify-between gap-3"><dt>Auth</dt><dd className="font-mono text-foreground">{log.auth_type || "—"}</dd></div><div className="flex justify-between gap-3"><dt>Request</dt><dd className="max-w-[160px] truncate font-mono text-foreground">{log.request_id || "—"}</dd></div>{log.source_ip && <div className="flex justify-between gap-3"><dt>IP</dt><dd className="font-mono text-foreground">{log.source_ip}</dd></div>}{log.user_agent && <div className="flex justify-between gap-3"><dt>Устройство</dt><dd className="max-w-[160px] truncate text-right text-foreground" title={log.user_agent}>{log.user_agent}</dd></div>}</dl></section>
        <section className="border-t border-border pt-4"><Button type="button" variant="ghost" size="sm" className="px-0 text-muted-foreground" onClick={() => setShowRaw((value) => !value)}><FileJson className="size-3.5" /> {showRaw ? "Скрыть raw JSON" : "Показать raw JSON"}</Button>{showRaw && <pre className="mt-3 max-h-72 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-[10px] leading-relaxed text-muted-foreground">{isFetching ? "Загрузка…" : JSON.stringify(raw ?? log.details ?? {}, null, 2)}</pre>}</section>
      </div>
    </aside>
  )
}
