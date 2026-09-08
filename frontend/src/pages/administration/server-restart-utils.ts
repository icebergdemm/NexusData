export type RestartStatus =
  | "disabled"
  | "scheduled"
  | "waiting_conditions"
  | "warning_sent"
  | "executing"
  | "server_offline"
  | "error"

export function formatRestartCountdown(nextRunAt: string | null, now = Date.now()): string {
  const target = nextRunAt ? Date.parse(nextRunAt) : NaN
  const remainingSeconds = Number.isFinite(target) ? Math.max(0, Math.floor((target - now) / 1000)) : 0
  const hours = Math.floor(remainingSeconds / 3600)
  const minutes = Math.floor((remainingSeconds % 3600) / 60)
  const seconds = remainingSeconds % 60
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":")
}

export function restartStatusLabel(status: RestartStatus): string {
  switch (status) {
    case "waiting_conditions": return "Ожидание условий"
    case "warning_sent": return "Предупреждение отправлено"
    case "executing": return "Ребут выполняется"
    case "server_offline": return "Сервер выключен"
    case "error": return "Ошибка"
    case "disabled": return "Выключен"
    default: return "Запланирован"
  }
}
