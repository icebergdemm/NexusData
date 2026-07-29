/** Справочники логов, общие для вкладки «Логи» и панели логов в просмотрщике
 *  демозаписи. Вынесены отдельно, чтобы списки типов действий и их цвета не
 *  разъезжались между двумя местами. */

export const ACTION_TYPES = [
  "picked_up", "placed", "removed", "opened", "closed", "equipped", "unequipped", "dropped",
  "reactor", "repair", "fabrication", "fabrication_cancel", "activated", "deactivated", "wire",
  "pump", "settings_change", "console", "death", "chat", "login", "logout", "attack",
  "structure", "kick", "name_change", "connection", "radio", "other",
]

export const ACTION_COLOR: Record<string, string> = {
  death: "text-destructive", kick: "text-destructive", attack: "text-destructive",
  login: "text-success", connection: "text-success",
  logout: "text-muted-foreground",
  chat: "text-info", radio: "text-info",
  console: "text-warning", settings_change: "text-warning",
}

export interface LogEvent {
  timestamp: string
  player_name: string | null
  action_type: string
  details: string | null
  server_name?: string | null
  source_file?: string | null
}
