export function lobbyServerStatusLabel(
  serverState: string | null,
  serverRunning: boolean | null,
  serverError: string | null,
): string {
  if (serverError) return "недоступен"
  if (serverRunning === true || serverState === "running") return "запущен"
  if (["offline", "stopped", "exited", "killed"].includes((serverState ?? "").toLowerCase())) return "выключен"
  if (serverState === "starting") return "запускается"
  if (serverState === "stopping") return "останавливается"
  return serverRunning === false ? "выключен" : "состояние неизвестно"
}

export function shouldShowLobbyCountdown(
  serverState: string | null,
  serverRunning: boolean | null,
  serverError: string | null,
): boolean {
  return !serverError && (serverRunning === true || serverState === "running")
}
