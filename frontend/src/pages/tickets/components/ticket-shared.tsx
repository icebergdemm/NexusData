import { AlertTriangle, Archive, Check, Clock3, Radio, ShieldCheck, UserRound, XCircle } from "lucide-react"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import type { TicketArchiveState, TicketArchiveSummary, TicketParticipant, TicketStatus } from "../types"
import { initials, ticketStatusLabel } from "./ticket-utils"

export function TicketStatusBadge({ status, compact = false }: { status: TicketStatus; compact?: boolean }) {
  if (status === "closed") {
    return <Badge variant="secondary" className={cn(compact && "px-1.5 py-0 text-[10px]")}><Archive />{ticketStatusLabel(status)}</Badge>
  }
  if (status === "waiting") {
    return <Badge variant="outline" className={cn("border-warning/40 text-warning", compact && "px-1.5 py-0 text-[10px]")}><Clock3 />{ticketStatusLabel(status)}</Badge>
  }
  return <Badge variant="default" className={cn(compact && "px-1.5 py-0 text-[10px]")}><Radio />{ticketStatusLabel(status)}</Badge>
}

const archiveLabels: Record<string, string> = {
  pending: "Архивация",
  complete: "Архив сохранён",
  partial: "Архив неполный",
  failed: "Ошибка архива",
  legacy: "Старый архив",
  incomplete: "Архив не завершён",
}

function ArchiveIcon({ state }: { state: TicketArchiveState }) {
  if (state === "complete") return <Check />
  if (state === "partial" || state === "legacy") return <AlertTriangle />
  if (state === "failed") return <XCircle />
  return <Clock3 />
}

export function ArchiveStatusBadge({
  archive,
  state,
  compact = false,
}: {
  archive?: TicketArchiveSummary | null
  state?: TicketArchiveState | null
  compact?: boolean
}) {
  // A manually deleted Discord channel is a ticket-level evidence exception.
  // It must remain visible even when the rows that happened to reach SQL look
  // complete, because Discord's final channel history can no longer be
  // audited after the deletion.
  const actualState = state === "incomplete" ? state : archive?.state ?? state
  if (!actualState) return null
  const isProblem = actualState === "failed" || actualState === "partial" || actualState === "incomplete"
  return (
    <Badge
      variant={isProblem ? "destructive" : actualState === "complete" ? "outline" : "secondary"}
      className={cn(
        actualState === "complete" && "border-success/40 text-success",
        actualState === "legacy" && "border-warning/40 text-warning",
        compact && "px-1.5 py-0 text-[10px]",
      )}
    >
      <ArchiveIcon state={actualState} />
      {archiveLabels[actualState] ?? actualState}
    </Badge>
  )
}

export function ArchiveStatusIndicator({ state }: { state: TicketArchiveState }) {
  const isProblem = state === "failed" || state === "partial" || state === "incomplete"
  return (
    <span
      className={cn(
        "inline-flex size-5 shrink-0 items-center justify-center border bg-muted/30 [&>svg]:size-3",
        state === "complete" && "border-success/40 text-success",
        (state === "pending" || state === "legacy") && "border-warning/40 text-warning",
        isProblem && "border-destructive/40 text-destructive",
      )}
      title={archiveLabels[state] ?? state}
    >
      <ArchiveIcon state={state} />
      <span className="sr-only">{archiveLabels[state] ?? state}</span>
    </span>
  )
}

export function PersonAvatar({
  name,
  url,
  size = "default",
  status,
  className,
}: {
  name: string | null | undefined
  url?: string | null
  size?: "sm" | "default" | "lg"
  status?: TicketParticipant["status"]
  className?: string
}) {
  const showPresence = status === "online" || status === "in_game"
  return (
    <div className="relative shrink-0">
      <Avatar size={size} className={className}>
        <AvatarImage src={url ?? undefined} alt="" />
        <AvatarFallback>{initials(name)}</AvatarFallback>
      </Avatar>
      {showPresence ? (
        <span
          className={cn(
            "absolute right-0 bottom-0 size-2.5 rounded-full border-2 border-background",
            status === "in_game" ? "bg-info" : "bg-success",
          )}
          title={status === "in_game" ? "В игре" : "Онлайн"}
        />
      ) : null}
    </div>
  )
}

export function ParticipantRoleIcon({ role }: { role: TicketParticipant["role"] }) {
  if (role === "assigned_admin" || role === "staff") return <ShieldCheck className="size-3.5" />
  return <UserRound className="size-3.5" />
}
