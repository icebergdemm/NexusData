import { Archive, CalendarClock, CheckCircle2, Hash, HardDrive, Paperclip, ShieldCheck, UserCheck, UsersRound } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { PlayerHoverLink } from "@/components/layout/PlayerHoverTooltip"
import { formatBrowserDate } from "@/lib/format-date"
import type { TicketDetailResponse, TicketParticipant } from "../types"
import {
  ArchiveStatusBadge,
  ParticipantRoleIcon,
  PersonAvatar,
  TicketStatusBadge,
} from "./ticket-shared"
import { formatBytes, participantName, participantRoleLabel } from "./ticket-utils"

function ParticipantRow({ participant }: { participant: TicketParticipant }) {
  const player = participant.player
  const playerId = player?.player_id ?? participant.player_id
  const name = participantName(participant)
  const playerName = player?.forced_display_name || player?.player_last_time_name
  return (
    <div className="flex gap-3 py-2.5">
      <PersonAvatar
        name={name}
        url={participant.avatar_url || participant.admin?.avatar_url || player?.avatar_url}
        status={player?.online ? "in_game" : participant.status}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          {playerId ? (
            <PlayerHoverLink playerId={playerId} className="min-w-0 truncate text-sm font-medium underline-offset-2 hover:underline">{name}</PlayerHoverLink>
          ) : <span className="min-w-0 truncate text-sm font-medium">{name}</span>}
          {participant.is_assigned ? <UserCheck className="size-3.5 shrink-0 text-success" aria-label="Ответственный" /> : null}
        </div>
        <div className="mt-0.5 flex items-center gap-1 text-[10px] text-muted-foreground">
          <ParticipantRoleIcon role={participant.role} />
          <span>{participantRoleLabel(participant)}</span>
          {participant.message_count != null ? <span>· {participant.message_count} сообщ.</span> : null}
        </div>
        {playerName ? (
          <p className="mt-0.5 truncate text-[10px] text-muted-foreground">Игрок: {playerName}{player?.online ? " · в игре" : ""}</p>
        ) : participant.status_label ? <p className="mt-0.5 text-[10px] text-muted-foreground">{participant.status_label}</p> : null}
      </div>
    </div>
  )
}

export function TicketContextPanel({
  data,
  canAssign,
  assignPending,
  onAssign,
}: {
  data: TicketDetailResponse
  canAssign: boolean
  assignPending: boolean
  onAssign: () => void
}) {
  const { ticket, archive } = data
  const participants = data.participants ?? []
  const assigned = ticket.assigned_admin
  const playerName = data.player?.forced_display_name || data.player?.player_last_time_name

  return (
    <aside aria-label="Контекст тикета" className="flex h-full min-h-0 flex-col bg-background">
      <div className="border-b px-4 py-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">Контекст</p>
        <h2 className="mt-1 text-sm font-semibold">Детали обращения</h2>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <section aria-labelledby="ticket-state-title" className="px-4 py-4">
          <h3 id="ticket-state-title" className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Состояние</h3>
          <div className="flex flex-wrap items-center gap-2">
            <TicketStatusBadge status={ticket.status} />
            {ticket.awaiting_admin && ticket.status !== "closed" ? <Badge variant="destructive">Нужен ответ</Badge> : null}
          </div>
          <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-xs">
            <dt className="flex items-center gap-1.5 text-muted-foreground"><Hash className="size-3.5" />Номер</dt>
            <dd className="text-right font-mono">{ticket.id}</dd>
            <dt className="flex items-center gap-1.5 text-muted-foreground"><CalendarClock className="size-3.5" />Создан</dt>
            <dd className="text-right">{formatBrowserDate(ticket.created_at)}</dd>
            {ticket.closed_at ? (
              <>
                <dt className="flex items-center gap-1.5 text-muted-foreground"><CheckCircle2 className="size-3.5" />Закрыт</dt>
                <dd className="text-right">{formatBrowserDate(ticket.closed_at)}</dd>
              </>
            ) : null}
          </dl>
        </section>

        <Separator />

        <section aria-labelledby="assignee-title" className="px-4 py-4">
          <h3 id="assignee-title" className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Ответственный</h3>
          {assigned ? (
            <div className="flex items-center gap-3">
              <PersonAvatar name={assigned.display_name} url={assigned.avatar_url} />
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{assigned.display_name || "Администратор"}</p>
                <p className="mt-0.5 flex items-center gap-1 text-[10px] text-muted-foreground"><ShieldCheck className="size-3" />{assigned.role || "Команда проекта"}</p>
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">Тикет ещё не назначен.</p>
          )}
          {canAssign && ticket.status !== "closed" ? (
            <Button type="button" variant={assigned ? "outline" : "secondary"} size="sm" className="mt-3 w-full" disabled={assignPending} onClick={onAssign}>
              <UserCheck data-icon="inline-start" />
              {assignPending ? "Назначение…" : assigned ? "Назначить на себя" : "Взять в работу"}
            </Button>
          ) : null}
        </section>

        <Separator />

        <section aria-labelledby="archive-title" className="px-4 py-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h3 id="archive-title" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Архив материалов</h3>
            <ArchiveStatusBadge archive={archive} state={ticket.archive_state} compact />
          </div>
          {archive ? (
            <dl className="grid grid-cols-2 gap-2 text-xs">
              <div className="border bg-muted/20 p-2">
                <dt className="flex items-center gap-1 text-[10px] text-muted-foreground"><Paperclip className="size-3" />Файлов</dt>
                <dd className="mt-1 font-mono text-sm">{archive.attachment_count}</dd>
              </div>
              <div className="border bg-muted/20 p-2">
                <dt className="flex items-center gap-1 text-[10px] text-muted-foreground"><HardDrive className="size-3" />Размер</dt>
                <dd className="mt-1 font-mono text-sm">{formatBytes(archive.total_bytes)}</dd>
              </div>
              {archive.failed_count ? <div className="col-span-2 border border-destructive/40 bg-destructive/10 p-2 text-destructive">Не сохранено файлов: {archive.failed_count}</div> : null}
              {archive.legacy_count ? <div className="col-span-2 border border-warning/40 bg-warning/10 p-2 text-warning">Старых внешних ссылок: {archive.legacy_count}</div> : null}
            </dl>
          ) : (
            <div className="flex items-start gap-2 text-xs text-muted-foreground"><Archive className="mt-0.5 size-4 shrink-0" /><p>Сводка архива станет доступна после обработки вложений.</p></div>
          )}
        </section>

        <Separator />

        <section aria-labelledby="participants-title" className="px-4 py-4">
          <div className="mb-1 flex items-center justify-between gap-2">
            <h3 id="participants-title" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Участники</h3>
            <span className="inline-flex items-center gap-1 font-mono text-[10px] text-muted-foreground"><UsersRound className="size-3.5" />{participants.length || 1}</span>
          </div>
          {participants.length ? (
            <div className="divide-y divide-border">{participants.map((participant, index) => <ParticipantRow key={participant.id ?? participant.discord_id ?? index} participant={participant} />)}</div>
          ) : (
            <div className="flex items-center gap-3 py-2.5">
              <PersonAvatar
                name={ticket.creator_display_name || ticket.creator_username}
                url={ticket.creator_avatar_url || data.player?.avatar_url || data.player?.steam_avatar_url}
                status={(data.player?.online ?? data.player?.is_online) ? "in_game" : undefined}
              />
              <div className="min-w-0">
                {data.player ? (
                  <PlayerHoverLink playerId={data.player.player_id} className="block truncate text-sm font-medium underline-offset-2 hover:underline">{ticket.creator_display_name || ticket.creator_username || playerName || "Автор тикета"}</PlayerHoverLink>
                ) : <p className="truncate text-sm font-medium">{ticket.creator_display_name || ticket.creator_username || "Автор тикета"}</p>}
                <p className="mt-0.5 text-[10px] text-muted-foreground">Автор тикета{playerName ? ` · ${playerName}` : ""}</p>
              </div>
            </div>
          )}
        </section>
      </div>
    </aside>
  )
}
