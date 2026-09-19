import { Monitor, Bot, KeyRound } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { actorName, actorSecondaryName } from "./audit-formatters"
import type { AuditSession } from "./audit-types"

function authLabel(value: string | null): string {
  if (value === "discord_oauth") return "Discord"
  if (value === "login_key" || value === "slogin") return "slogin"
  return value || "браузер"
}

export function AuditSessionRail({
  sessions,
  selectedSessionId,
  onSelect,
}: {
  sessions: AuditSession[]
  selectedSessionId: number | null
  onSelect: (session: AuditSession) => void
}) {
  return (
    <aside className="min-w-0 border-b border-border bg-card/20 xl:border-b-0 xl:border-r">
      <div className="border-b border-border px-4 py-3">
        <div className="font-semibold">Сессии</div>
        <div className="mt-0.5 text-xs text-muted-foreground">{sessions.length} цепочек доступа</div>
      </div>
      <div className="flex overflow-x-auto xl:block xl:overflow-visible">
        {sessions.map((session) => {
          const selected = session.session_id === selectedSessionId
          const name = actorName(session.actor, session.status === "actor" ? "Discord-актор" : "Администратор")
          const secondary = actorSecondaryName(session.actor)
          const Icon = session.status === "actor" ? Bot : session.auth_type === "login_key" ? KeyRound : Monitor
          return (
            <button
              key={`${session.session_id ?? "actor"}-${session.actor.discord_id ?? session.actor.type}`}
              type="button"
              onClick={() => onSelect(session)}
              className={cn(
                "group min-w-[220px] border-r border-border px-4 py-3 text-left transition-colors last:border-r-0 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring xl:min-w-0 xl:border-r-0 xl:border-b",
                selected && "bg-primary/10 shadow-[inset_3px_0_0_hsl(var(--primary))]",
              )}
              aria-pressed={selected}
            >
              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-secondary text-muted-foreground"><Icon className="size-3.5" /></span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{name}</span>
                  {secondary && <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">NexusData: {secondary}</span>}
                </span>
                {session.status === "active" && <span className="mt-1 size-1.5 rounded-full bg-success" aria-label="Активна" />}
              </div>
              <span className="mt-2 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
                <span>{session.session_id ? authLabel(session.auth_type) : "Discord-бот"}</span>
                <span>{session.event_count} событий</span>
              </span>
              <span className="mt-1 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
                <span>{session.last_event_at ? new Date(session.last_event_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : "нет событий"}</span>
                {session.status !== "actor" && <Badge variant={session.status === "active" ? "default" : "secondary"} className="h-5 px-1.5 text-[10px]">{session.status === "active" ? "активна" : session.status === "expired" ? "истекла" : "отозвана"}</Badge>}
              </span>
            </button>
          )
        })}
        {!sessions.length && <div className="p-4 text-sm text-muted-foreground">Цепочек доступа не найдено.</div>}
      </div>
    </aside>
  )
}
