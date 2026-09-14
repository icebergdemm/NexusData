export type VoiceChannel = "local" | "radio"
export type VoiceChannelFilter = "all" | VoiceChannel
export type VoiceCategoryFilter = "all" | "voice"

export interface VoiceSpeaker {
  speaker_id: number
  names?: string[]
  player_id?: number | null
  steam_id?: string | null
  resolution_status?: string | null
}

export interface VoiceSegmentInput {
  speaker_id: number
  start_ms: number
  end_ms: number
  radio?: boolean
}

export interface VoiceSegment {
  speakerId: number
  startMs: number
  endMs: number
  radio: boolean
}

export interface VoiceLane {
  speakerId: number
  name: string
  playerId: number | null
  steamId: string | null
  resolutionStatus: string | null
  segments: VoiceSegment[]
  totalMs: number
}

export interface VoiceLaneFilter {
  category: VoiceCategoryFilter
  channel: VoiceChannelFilter
  activeOnly: boolean
  speakerIds: ReadonlySet<number>
}

function displayName(speaker: VoiceSpeaker): string {
  const name = speaker.names?.find((value) => value.trim())
  return name?.trim() || `Игрок #${speaker.speaker_id}`
}

function normalizedSegment(input: VoiceSegmentInput, durationMs: number): VoiceSegment | null {
  const startMs = Math.max(0, Math.min(durationMs, Math.round(Number(input.start_ms))))
  const endMs = Math.max(startMs + 1, Math.min(durationMs, Math.round(Number(input.end_ms))))
  if (endMs <= startMs || durationMs <= 0) return null
  return {
    speakerId: Number(input.speaker_id),
    startMs,
    endMs,
    radio: Boolean(input.radio),
  }
}

function mergeSegments(segments: VoiceSegment[]): VoiceSegment[] {
  const ordered = [...segments].sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs)
  const merged: VoiceSegment[] = []
  for (const segment of ordered) {
    const previous = merged.at(-1)
    if (previous && previous.radio === segment.radio && segment.startMs <= previous.endMs + 1) {
      previous.endMs = Math.max(previous.endMs, segment.endMs)
    } else {
      merged.push({ ...segment })
    }
  }
  return merged
}

export function buildVoiceLanes(
  speakers: readonly VoiceSpeaker[],
  segments: readonly VoiceSegmentInput[],
  durationMs: number,
): VoiceLane[] {
  const safeDurationMs = Math.max(0, Math.round(Number(durationMs) || 0))
  const bySpeaker = new Map<number, VoiceSegment[]>()
  for (const input of segments) {
    const speakerId = Number(input.speaker_id)
    const segment = normalizedSegment(input, safeDurationMs)
    if (segment) bySpeaker.set(speakerId, [...(bySpeaker.get(speakerId) ?? []), segment])
  }

  const byId = new Map(speakers.map((speaker) => [Number(speaker.speaker_id), speaker]))
  for (const speakerId of bySpeaker.keys()) {
    if (!byId.has(speakerId)) byId.set(speakerId, { speaker_id: speakerId })
  }

  return [...byId.values()]
    .map((speaker) => {
      const laneSegments = mergeSegments(bySpeaker.get(Number(speaker.speaker_id)) ?? [])
      return {
        speakerId: Number(speaker.speaker_id),
        name: displayName(speaker),
        playerId: speaker.player_id ?? null,
        steamId: speaker.steam_id ?? null,
        resolutionStatus: speaker.resolution_status ?? null,
        segments: laneSegments,
        totalMs: laneSegments.reduce((total, segment) => total + segment.endMs - segment.startMs, 0),
      }
    })
    .sort((a, b) => a.name.localeCompare(b.name, "ru") || a.speakerId - b.speakerId)
}

export function filterVoiceLanes(lanes: readonly VoiceLane[], filter: VoiceLaneFilter): VoiceLane[] {
  return lanes.flatMap((lane) => {
    if (filter.speakerIds.size > 0 && !filter.speakerIds.has(lane.speakerId)) return []
    const channelSegments = filter.channel === "all"
      ? lane.segments
      : lane.segments.filter((segment) => (filter.channel === "radio") === segment.radio)
    if (filter.activeOnly && channelSegments.length === 0) return []
    return [{ ...lane, segments: channelSegments, totalMs: channelSegments.reduce((total, segment) => total + segment.endMs - segment.startMs, 0) }]
  })
}

export function voiceViewFromSearch(search: string): "full" | "voice" {
  return new URLSearchParams(search).get("view") === "voice" ? "voice" : "full"
}

export function replayDisplayMode(
  replayKind: "round" | "lobby" | "lobby_dump",
  search: string,
): "full" | "voice" {
  if (replayKind === "lobby" || replayKind === "lobby_dump") return "voice"
  return voiceViewFromSearch(search)
}
