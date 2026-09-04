import type { ReactNode } from "react"
import { ChevronRight, MessageCircleMore, Paperclip, SearchX, UserRound } from "lucide-react"
import { Link } from "react-router-dom"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import { formatBrowserDate } from "@/lib/format-date"
import { cn } from "@/lib/utils"
import type { TicketListStatus, TicketRow, TicketTypeMeta } from "../types"
import { ArchiveStatusBadge, ArchiveStatusIndicator, TicketStatusBadge } from "./ticket-shared"

function displayName(ticket: TicketRow): string {
  return ticket.creator_display_name || ticket.creator_username || "Неизвестный пользователь"
}

export function TicketInboxList({
  tickets,
  ticketTypes,
  activeId,
  compact = false,
  onNavigate,
  returnStatus,
  renderActions,
}: {
  tickets: TicketRow[]
  ticketTypes: Record<string, TicketTypeMeta>
  activeId?: number
  compact?: boolean
  onNavigate?: () => void
  returnStatus?: TicketListStatus | null
  renderActions?: (ticket: TicketRow) => ReactNode
}) {
  if (!tickets.length) {
    return (
      <div className="flex min-h-52 flex-col items-center justify-center gap-3 px-6 text-center text-muted-foreground">
        <SearchX className="size-7" aria-hidden="true" />
        <div>
          <p className="text-sm font-medium text-foreground">Тикеты не найдены</p>
          <p className="mt-1 text-xs">Измените статус или поисковый запрос.</p>
        </div>
      </div>
    )
  }

  return (
    <nav aria-label="Список тикетов" className="divide-y divide-border">
      {tickets.map((ticket) => {
        const meta = ticketTypes[ticket.ticket_type]
        const selected = ticket.id === activeId
        const actions = compact ? null : renderActions?.(ticket)
        return (
          <div key={ticket.id} className="group relative min-w-0">
            <Link
              to={`/tickets/${ticket.id}${returnStatus ? `?from=${encodeURIComponent(returnStatus)}` : ""}`}
              onClick={onNavigate}
              aria-current={selected ? "page" : undefined}
              className={cn(
                "relative flex min-w-0 gap-3 border-l-2 border-l-transparent px-3 py-3 transition-colors hover:bg-accent/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                selected && "border-l-primary bg-accent",
                compact ? "min-h-[5.25rem]" : "min-h-[5.75rem] sm:px-4",
                actions && "pr-12 sm:pr-14",
              )}
            >
              <div className="flex size-9 shrink-0 items-center justify-center border bg-muted text-base" aria-hidden="true">
                <PlatformIcon value={meta?.emoji ?? "🎫"} fallback={MessageCircleMore} className="size-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-start gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm font-semibold">
                    <span className="mr-1.5 font-mono text-[11px] text-muted-foreground">#{ticket.id}</span>
                    {ticket.title}
                  </span>
                  <time className="shrink-0 font-mono text-[10px] text-muted-foreground" dateTime={ticket.last_message_at ?? ticket.created_at ?? undefined}>
                    {formatBrowserDate(ticket.last_message_at ?? ticket.created_at, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                  </time>
                </div>
                <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                  <UserRound className="size-3" aria-hidden="true" />
                  <span className="truncate">{displayName(ticket)}</span>
                  {ticket.awaiting_admin && ticket.status !== "closed" ? (
                    <span className="ml-auto size-2 shrink-0 rounded-full bg-destructive" title="Ожидает ответа команды" />
                  ) : null}
                </div>
                {ticket.last_message_preview ? (
                  <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{ticket.last_message_preview}</p>
                ) : null}
                <div className="mt-2 flex min-w-0 items-center gap-1.5">
                  <TicketStatusBadge status={ticket.status} compact />
                  {ticket.deleted_at ? <Badge variant="destructive" className="px-1.5 py-0 text-[10px]">Удалён</Badge> : null}
                  {ticket.attachment_count ? (
                    <Badge variant="outline" className="px-1.5 py-0 text-[10px]"><Paperclip />{ticket.attachment_count}</Badge>
                  ) : null}
                  {ticket.archive_state ? (compact ? <ArchiveStatusIndicator state={ticket.archive_state} /> : <ArchiveStatusBadge state={ticket.archive_state} compact />) : null}
                  {ticket.unread_count ? (
                    <Badge variant="destructive" className="ml-auto min-w-5 px-1.5 py-0 text-[10px]">{ticket.unread_count}</Badge>
                  ) : null}
                </div>
              </div>
              {!compact && !actions ? <ChevronRight className="mt-1 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden="true" /> : null}
            </Link>
            {actions ? (
              <div className="absolute top-1/2 right-2 z-10 -translate-y-1/2 sm:right-3">
                {actions}
              </div>
            ) : null}
          </div>
        )
      })}
    </nav>
  )
}

export function TicketInboxSkeleton({ compact = false }: { compact?: boolean }) {
  return (
    <div className="divide-y divide-border" aria-label="Загрузка тикетов">
      {Array.from({ length: compact ? 7 : 5 }).map((_, index) => (
        <div key={index} className="flex gap-3 px-3 py-3">
          <Skeleton className="size-9 shrink-0" />
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <Skeleton className="h-3 w-3/4" />
            <Skeleton className="h-2.5 w-1/2" />
            <Skeleton className="h-4 w-2/5" />
          </div>
        </div>
      ))}
    </div>
  )
}
