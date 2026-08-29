/** Справочники логов, общие для вкладки «Логи» и панели логов в просмотрщике
 *  демозаписи. Вынесены отдельно, чтобы списки типов действий и их цвета не
 *  разъезжались между двумя местами. */

// "used" отсутствовал здесь (и в бэкендовом дубликате категорий — см.
// routes/logs.py, теперь удалён), из-за чего события "used" не попадали ни
// в выпадающий список типов, ни в категорию "Игровые"/"Технические" —
// часть жалобы "неправильно работает поиск по логам/категориям".
//
// Вторая партия (threw..command_denied) добавлена по итогам разбора
// реальных серверных логов (services_logs.py, ACTION_PATTERNS) — эти
// события раньше не распознавались НИКАКИМ паттерном и молча падали в
// "other" без привязки к игроку: авто-кики по сети, кики/баны через админ-
// консоль, вход на сервер, готовность к раунду, респавн и т.д. — часть
// самых частых событий в реальных логах оставались непоисковыми.
export const ACTION_TYPES = [
  "picked_up", "placed", "removed", "opened", "closed", "equipped", "unequipped", "dropped",
  "used", "reactor", "repair", "fabrication", "fabrication_cancel", "activated", "deactivated", "wire",
  "pump", "settings_change", "console", "death", "chat", "login", "logout", "attack",
  "structure", "kick", "name_change", "connection", "radio", "other",
  "threw", "repair_failed", "attached", "command_denied", "terminal", "joined", "ready",
  "respawn", "name_history", "talent_gained", "looted_body", "ban",
]

export const ACTION_COLOR: Record<string, string> = {
  death: "text-destructive", kick: "text-destructive", attack: "text-destructive", ban: "text-destructive",
  login: "text-success", connection: "text-success", joined: "text-success", ready: "text-success",
  logout: "text-muted-foreground",
  chat: "text-info", radio: "text-info", terminal: "text-info",
  console: "text-warning", settings_change: "text-warning", command_denied: "text-warning",
  talent_gained: "text-success", respawn: "text-success",
}

export interface LogEvent {
  event_id?: number
  timestamp: string
  player_name: string | null
  /** Резолвится бэкендом по логам-сохранённой связке имя→SteamID → PlayerData
   * (routes/logs.py) — null, если игрок не опознан (например, имя встречено
   * только как упоминание в тексте чужого события). Есть значение → делаем
   * "Игрок" кликабельной ссылкой на профиль. */
  player_id?: number | null
  action_type: string
  details: string | null
  server_name?: string | null
  source_file?: string | null
}
