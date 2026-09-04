import type { TicketParticipant, TicketRow, TicketStatus } from "../types"

export function ticketStatusLabel(status: TicketStatus): string {
  if (status === "open") return "Открыт"
  if (status === "waiting") return "Ожидает"
  if (status === "closed") return "Закрыт"
  return status || "Неизвестно"
}

export function initials(name: string | null | undefined): string {
  const clean = (name || "?").trim()
  const parts = clean.split(/\s+/).filter(Boolean)
  if (parts.length > 1) return `${parts[0][0]}${parts[1][0]}`.toUpperCase()
  return clean.slice(0, 2).toUpperCase()
}

export function participantName(participant: TicketParticipant | null | undefined): string {
  return participant?.display_name || participant?.username || participant?.admin?.display_name || participant?.player?.forced_display_name || participant?.player?.player_last_time_name || "Неизвестный участник"
}

export function participantRoleLabel(participant: TicketParticipant): string {
  if (participant.role_label) return participant.role_label
  if (participant.role === "creator") return "Автор тикета"
  if (participant.role === "assigned_admin" || participant.is_assigned) return "Ответственный"
  if (participant.role === "staff" || participant.admin) return "Команда проекта"
  return "Участник"
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || Number.isNaN(bytes)) return "—"
  if (bytes < 1024) return `${bytes} Б`
  const units = ["КБ", "МБ", "ГБ", "ТБ"]
  let value = bytes / 1024
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`
}

export function filterTickets(tickets: TicketRow[], query: string, status: string): TicketRow[] {
  const normalized = query.trim().toLocaleLowerCase("ru")
  return tickets.filter((ticket) => {
    if (status === "open" && ticket.status === "closed") return false
    if (status === "closed" && ticket.status !== "closed") return false
    if (!normalized) return true
    return [
      String(ticket.id),
      ticket.title,
      ticket.ticket_type,
      ticket.creator_display_name,
      ticket.creator_username,
      ticket.last_message_preview,
    ].some((value) => value?.toLocaleLowerCase("ru").includes(normalized))
  })
}
