export type RatingMode = "mission" | "pvp"
export type RatingMetadataValue = string | number | boolean | null
export type RatingMetadata = [string, RatingMetadataValue][]

export interface RatingLeaderboardRow {
  rank: number
  steamId: string
  playerName: string
  role: string | null
  rating: number | null
  points: number | null
  activity: number | null
  playtimeHours: number | null
  qualifiedRounds: number | null
  confidence: number | null
  eligibility: string | null
  dataQuality: string | null
  eventCount: number | null
  lastActivity: string | null
  extra: RatingMetadata
}

export interface RatingLeaderboard {
  leaderboard: RatingLeaderboardRow[]
  roles: string[]
  selectedRole: string
  pagination: RatingPagination
  extra: RatingMetadata
}

export interface RatingPagination {
  total: number
  totalPages: number
  page: number
  pageSize: number
  hasNext: boolean
}

export interface RatingProfile {
  id: string
  label: string
}

type UnknownRecord = Record<string, unknown>

const leaderboardKeys = new Set([
  "leaderboard", "entries", "roles", "selected_role", "selectedRole", "role", "mode", "pagination",
  "total", "total_pages", "page", "page_size", "has_next",
  "steam_id", "steamId", "player_name", "playerName", "name", "rank", "rating",
  "current_rating", "points", "cumulative_points", "last_activity", "last_activity_date", "last_event_at",
  "activity", "activity_factor", "play_time_hours", "playtime_hours", "qualified_rounds", "confidence", "eligibility", "event_count", "unmapped_events", "data_quality", "source",
])

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {}
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null
}

function transparencyFields(value: UnknownRecord, known: Set<string>): RatingMetadata {
  return Object.entries(value).flatMap(([key, field]) => (
    !known.has(key) && (typeof field === "string" || typeof field === "number" || typeof field === "boolean" || field === null)
      ? [[key, field] as [string, RatingMetadataValue]]
      : []
  ))
}

export function buildRatingV2Query({ mode, role, page, pageSize, steamId }: { mode: RatingMode; role: string; page?: number; pageSize?: number; steamId?: string }): string {
  const query = new URLSearchParams({ mode, role })
  if (page != null) query.set("page", String(Math.max(1, Math.trunc(page))))
  if (pageSize != null) query.set("page_size", String(Math.max(1, Math.trunc(pageSize))))
  if (steamId?.trim()) query.set("steam_id", steamId.trim())
  return query.toString()
}

export function normalizeRatingLeaderboard(value: unknown): RatingLeaderboard {
  const response = asRecord(value)
  const entries = Array.isArray(response.leaderboard) ? response.leaderboard : Array.isArray(response.entries) ? response.entries : []
  const paginationResponse = asRecord(response.pagination)
  const page = asNumber(response.page) ?? asNumber(paginationResponse.page) ?? 1
  const pageSize = asNumber(response.page_size) ?? asNumber(paginationResponse.page_size) ?? 100
  const total = asNumber(response.total) ?? asNumber(paginationResponse.total) ?? entries.length
  const totalPages = asNumber(response.total_pages) ?? asNumber(paginationResponse.total_pages) ?? Math.max(1, Math.ceil(total / Math.max(1, pageSize)))
  const hasNext = typeof response.has_next === "boolean" ? response.has_next : typeof paginationResponse.has_next === "boolean" ? paginationResponse.has_next : page < totalPages
  const leaderboard = entries.flatMap((entry, index) => {
    const row = asRecord(entry)
    const steamId = asString(row.steam_id) ?? asString(row.steamId) ?? ""
    if (!steamId) return []
    return [{
      rank: asNumber(row.rank) ?? (page - 1) * pageSize + index + 1,
      steamId,
      playerName: asString(row.player_name) ?? asString(row.playerName) ?? asString(row.name) ?? steamId,
      role: asString(row.role) ?? asString(row.job) ?? asString(row.character_role),
      rating: asNumber(row.rating) ?? asNumber(row.current_rating),
      points: asNumber(row.cumulative_points) ?? asNumber(row.points),
      activity: asNumber(row.activity_factor) ?? asNumber(row.activity),
      playtimeHours: asNumber(row.play_time_hours) ?? asNumber(row.playtime_hours),
      qualifiedRounds: asNumber(row.qualified_rounds),
      confidence: asNumber(row.confidence),
      eligibility: asString(row.eligibility),
      dataQuality: asString(row.data_quality) ?? asString(row.dataQuality),
      eventCount: asNumber(row.event_count),
      lastActivity: asString(row.last_activity) ?? asString(row.last_activity_date) ?? asString(row.last_event_at),
      extra: transparencyFields(row, leaderboardKeys),
    }]
  })
  const roles = Array.isArray(response.roles) ? response.roles.filter((role): role is string => typeof role === "string") : []
  const rowRoles = leaderboard.flatMap((row) => row.role ? [row.role] : [])
  const allRoles = Array.from(new Set([...roles, ...rowRoles]))
  const selectedRole = asString(response.selected_role) ?? asString(response.selectedRole) ?? asString(response.role) ?? (allRoles.length === 1 ? allRoles[0] : "all")
  return {
    leaderboard,
    roles: allRoles,
    selectedRole,
    pagination: { total, totalPages, page, pageSize, hasNext },
    extra: transparencyFields(response, leaderboardKeys),
  }
}

export function getRatingProfiles(value: unknown): RatingProfile[] {
  const response = asRecord(value)
  const profiles = response.profiles ?? asRecord(response.config).profiles
  if (Array.isArray(profiles)) {
    return profiles.flatMap((profile) => {
      if (typeof profile === "string") return [{ id: profile, label: profile }]
      const item = asRecord(profile)
      const id = asString(item.id) ?? asString(item.name)
      return id ? [{ id, label: asString(item.label) ?? asString(item.name) ?? id }] : []
    })
  }
  return Object.entries(asRecord(profiles)).map(([id, profile]) => {
    const item = asRecord(profile)
    return { id, label: asString(item.label) ?? asString(item.name) ?? id }
  })
}

export function prepareRatingConfigPayload(value: unknown, profileId: string): UnknownRecord {
  const response = asRecord(value)
  const nestedConfig = asRecord(response.config)
  if (Object.keys(nestedConfig).length > 0) {
    return { ...response, config: { ...nestedConfig, ...(profileId ? { active_profile: profileId } : {}) } }
  }
  return { ...response, ...(profileId ? { active_profile: profileId } : {}) }
}
