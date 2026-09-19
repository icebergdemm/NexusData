import { ChevronRight } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { actorOf, actorName, actorSecondaryName, eventDotClass, eventLabel, eventTarget, eventToneClass, formatEventDate, formatEventTime } from "./audit-formatters"
import type { AuditLog } from "./audit-types"

export function AuditTimeline({ logs, selectedEventId, onSelect }: { logs: AuditLog[]; selectedEventId: number | null; onSelect: (log: AuditLog) => void }) {
  let lastDay = ""
  return (
    <div className="space-y-1 p-4 sm:p-5">
      {!logs.length && <div className="border-y border-border py-12 text-center text-sm text-muted-foreground">В этой цепочке событий нет.</div>}
      {logs.map((log) => {
        const day = formatEventDate(log.created_at)
        const showDay = day !== lastDay
        lastDay = day
        const actor = actorOf(log)
        const secondary = actorSecondaryName(actor)
        const target = eventTarget(log)
        return (
          <div key={log.id}>
            {showDay && <div className="mb-2 ml-[76px] pt-2 text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">{day}</div>}
            <div className="grid grid-cols-[58px_14px_minmax(0,1fr)] gap-2.5 pb-3">
              <time className="pt-2 text-right font-mono text-[10px] text-muted-foreground">{formatEventTime(log.created_at)}</time>
              <div className="relative flex justify-center">
                <span className={cn("z-10 mt-2 size-2.5 rounded-full border-2 border-card", eventDotClass(log.action))} />
                <span className="absolute top-4 bottom-[-12px] border-l border-border" aria-hidden="true" />
              </div>
              <button
                type="button"
                onClick={() => onSelect(log)}
                className={cn("min-w-0 rounded-md border border-border bg-card px-3 py-2.5 text-left transition-colors hover:border-primary/60 hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", selectedEventId === log.id && "border-primary bg-primary/10")}
                aria-pressed={selectedEventId === log.id}
              >
                <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span className="text-sm font-semibold">{eventLabel(log)}</span>
                  <span className={cn("font-mono text-[10px]", eventToneClass(log.action))}>{log.action}</span>
                </span>
                <span className="mt-1 block text-xs text-foreground/80">{target || log.reason || "Действие зафиксировано"}</span>
                <span className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
                  <span>{actorName(actor)}</span>
                  {secondary && <span>NexusData: {secondary}</span>}
                  <span>{log.structured_details?.context.source ?? (log.action.startsWith("discord:") ? "Discord" : "Панель")}</span>
                  <Badge variant={log.outcome === "failure" ? "destructive" : "secondary"} className="h-5 px-1.5 text-[10px]">{log.outcome === "failure" ? "отклонено" : "успешно"}</Badge>
                  <ChevronRight className="ml-auto size-3.5" aria-hidden="true" />
                </span>
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
