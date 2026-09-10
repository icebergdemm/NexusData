export interface HealthzState {
  status: "ok" | "maintenance" | "degraded"
  db_healthy: boolean
  api_enabled: boolean
  web_enabled: boolean
  maintenance_message: string | null
}

export function maintenanceMessageFromHealth(data: HealthzState | null): string | null {
  if (!data || data.status === "ok") return null
  return data.maintenance_message
    || (!data.db_healthy ? "База данных недоступна. Мы уже работаем над восстановлением." : "Сайт временно недоступен.")
}
