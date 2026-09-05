export const communicationChannelLabels: Record<string, string> = {
  default: "локальный чат",
  local: "локальный голос",
  radio: "радио",
  private: "личный чат",
  team: "командный чат",
  dead: "чат мёртвых",
}

export const communicationContextLabels: Record<string, string> = {
  round: "раунд",
  lobby: "лобби",
  unmatched: "раунд не сопоставлен",
}

export function communicationContextSummary(context: string): string {
  return communicationContextLabels[context] ?? "контекст неизвестен"
}

export function recipientTextVariant(originalText: string | null, deliveredText: string | null): string | null {
  if (!deliveredText) return null
  if (originalText?.trim() === deliveredText.trim()) return null
  return deliveredText
}

export interface CommunicationFilters {
  search: string
  source: string
  channel: string
  server: string
  asrStatus: string
  context: string
}

export function buildCommunicationQuery(filters: CommunicationFilters, page: number): string {
  const params = new URLSearchParams({ page: String(page), page_size: "100" })
  if (filters.search) params.set("search", filters.search)
  if (filters.source !== "all") params.set("source", filters.source)
  if (filters.channel !== "all") params.set("channel", filters.channel)
  if (filters.server !== "all") params.set("server", filters.server)
  if (filters.asrStatus !== "all") params.set("asr_status", filters.asrStatus)
  if (filters.context !== "all") params.set("context", filters.context)
  return params.toString()
}
