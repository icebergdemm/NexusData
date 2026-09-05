export const communicationChannelLabels: Record<string, string> = {
  default: "локальный",
  local: "локальный",
  radio: "радио",
  private: "личный",
  team: "команда",
  dead: "мёртвые",
}

export interface CommunicationFilters {
  search: string
  source: string
  channel: string
  server: string
  asrStatus: string
  lobby: boolean
}

export function buildCommunicationQuery(filters: CommunicationFilters, page: number): string {
  const params = new URLSearchParams({ page: String(page), page_size: "100" })
  if (filters.search) params.set("search", filters.search)
  if (filters.source !== "all") params.set("source", filters.source)
  if (filters.channel !== "all") params.set("channel", filters.channel)
  if (filters.server !== "all") params.set("server", filters.server)
  if (filters.asrStatus !== "all") params.set("asr_status", filters.asrStatus)
  if (filters.lobby) params.set("lobby", "true")
  return params.toString()
}
