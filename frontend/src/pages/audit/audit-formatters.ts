import { formatBrowserDate } from "@/lib/format-date"
import type { AuditActor, AuditLog } from "./audit-types"

export const actionLabels: Record<string, string> = {
  login: "Вход в панель",
  logout: "Выход из панели",
  csrf_failure: "Ошибка проверки CSRF",
  login_key_failure: "Неудачный вход по ключу",
  login_key_create: "Создан ключ входа",
  login_key_revoke: "Отозван ключ входа",
  login_key_use: "Использован ключ входа",
  revoke_session: "Отозвана сессия",
  task_delete: "Удалена задача",
  ticket_delete: "Удалён тикет",
  punishment_create: "Создано наказание",
  punishment_void: "Аннулировано наказание",
  "moderation.kick": "Исключён игрок с сервера",
  "moderation.ban": "Заблокирован игрок",
  view_player_profile: "Открыт профиль игрока",
  view_player_ip: "Просмотрен IP игрока",
  web_lockdown_change: "Изменён режим шатдауна",
}

export function actionLabel(action: string): string {
  return actionLabels[action] ?? action.replaceAll("_", " ").replaceAll(".", " · ")
}

export function actorOf(log: AuditLog): AuditActor {
  return log.actor ?? {
    type: log.admin_id ? "web" : "system",
    discord_id: null,
    discord_name: log.admin_display_name,
    nexus_username: log.admin_name,
  }
}

export function actorName(actor: AuditActor, fallback = "Системное событие"): string {
  return actor.discord_name?.trim() || (actor.discord_id ? `Discord-актор · ID ${actor.discord_id}` : fallback)
}

export function actorSecondaryName(actor: AuditActor): string | null {
  const value = actor.nexus_username?.trim()
  return value && value !== actor.discord_name?.trim() ? value : null
}

export function eventLabel(log: AuditLog): string {
  return log.structured_details?.summary || actionLabel(log.action)
}

export function eventTarget(log: AuditLog): string | null {
  const target = log.structured_details?.target
  if (target?.label) return target.label
  if (target?.id != null) return `${target.type} #${target.id}`
  const details = log.details ?? {}
  if (details.reason) return String(details.reason)
  return null
}

export function eventDotClass(action: string): string {
  const value = action.toLowerCase()
  if (/(delete|remove|revoke|void|kick|ban)/.test(value)) return "bg-destructive"
  if (/(create|add|grant)/.test(value)) return "bg-success"
  if (/(failure|csrf|auth)/.test(value)) return "bg-warning"
  return "bg-primary"
}

export function eventToneClass(action: string): string {
  const value = action.toLowerCase()
  if (/(delete|remove|revoke|void|kick|ban)/.test(value)) return "text-destructive"
  if (/(create|add|grant)/.test(value)) return "text-success"
  if (/(failure|csrf|auth)/.test(value)) return "text-warning"
  return "text-primary"
}

export function formatEventDate(value: string | null): string {
  return formatBrowserDate(value, { day: "2-digit", month: "2-digit", year: "numeric" })
}

export function formatEventTime(value: string | null): string {
  return formatBrowserDate(value, { hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

export function formatChangeValue(value: unknown): string {
  if (value === null || value === undefined) return "—"
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}
