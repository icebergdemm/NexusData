export interface ReplayContextEvent {
  id: string
  offset_ms: number
  player_name: string | null
  action_type: string
  details: string | null
  transcript_text?: string | null
  asr_status?: string | null
  asr_confidence?: number | null
}

export function sortContextEvents(events: readonly ReplayContextEvent[]): ReplayContextEvent[] {
  return [...events].sort((left, right) => left.offset_ms - right.offset_ms || left.id.localeCompare(right.id))
}

export function activeContextEventIndex(events: readonly ReplayContextEvent[], timeMs: number): number {
  let low = 0
  let high = events.length - 1
  let found = -1
  while (low <= high) {
    const middle = (low + high) >> 1
    if (events[middle].offset_ms <= timeMs) {
      found = middle
      low = middle + 1
    } else {
      high = middle - 1
    }
  }
  return found
}

export function contextEventText(event: Pick<ReplayContextEvent, "details" | "transcript_text">): string {
  return (event.transcript_text ?? event.details ?? "").trim()
}

export function contextEventIsPast(event: Pick<ReplayContextEvent, "offset_ms">, timeMs: number): boolean {
  return event.offset_ms <= timeMs
}
