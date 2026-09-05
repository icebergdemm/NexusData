export type DiscordLinkFilter = "all" | "linked" | "unlinked"
export type FilterMode = "all" | "with" | "without"
export type LabelFilterMode = FilterMode | "any"
export type ActivityFilter = "all" | "new" | "inactive_7" | "inactive_30" | "inactive_90"

export type PlayerSortKey =
  | "none"
  | "server"
  | "join_time"
  | "first_login"
  | "last_login"
  | "last_activity"
  | "play_time"
  | "active_sanctions"
  | "open_anomalies"
  | "discord_link"
  | "label_count"

export type SortDirection = "asc" | "desc"

export interface PlayerSort {
  key: PlayerSortKey
  direction: SortDirection
}

export interface PlayerFilterState {
  discordFilter: DiscordLinkFilter
  labelMode: LabelFilterMode
  labelIds: string[]
  rankId: string
  notesMode: FilterMode
  sanctionsMode: FilterMode
  anomaliesMode: FilterMode
  nameHistoryMode: FilterMode
  ipHistoryMode: FilterMode
  activityMode: ActivityFilter
}

export interface FilterablePlayer {
  has_discord_link: boolean
  has_labels?: boolean
  has_notes?: boolean
  has_active_punishments?: boolean
  has_open_anomalies?: boolean
  has_previous_names?: boolean
  has_ip_history?: boolean
  label_ids?: readonly number[]
  rank_id?: number | null
  player_steamid?: string
  player_name?: string | null
  player_last_time_name?: string | null
  server_name?: string | null
  join_time?: string | null
  first_login?: string | null
  last_login_time?: string | null
  last_activity?: string | null
  is_new_player?: boolean | null
  play_time?: number | null
  total_play_time?: number | null
  play_time_hours?: number | null
}

export function getPlayerFilterDefaults(): PlayerFilterState {
  return {
    discordFilter: "all",
    labelMode: "all",
    labelIds: [],
    rankId: "all",
    notesMode: "all",
    sanctionsMode: "all",
    anomaliesMode: "all",
    nameHistoryMode: "all",
    ipHistoryMode: "all",
    activityMode: "all",
  }
}

export function resetPlayerFilters(): PlayerFilterState {
  return getPlayerFilterDefaults()
}

export function countActivePlayerFilters(filters: PlayerFilterState, sort: PlayerSort, defaultSort: PlayerSort): number {
  return (filters.discordFilter !== "all" ? 1 : 0)
    + (filters.labelMode !== "all" && (filters.labelMode !== "any" || filters.labelIds.length > 0) ? 1 : 0)
    + (filters.rankId !== "all" ? 1 : 0)
    + (filters.notesMode !== "all" ? 1 : 0)
    + (filters.sanctionsMode !== "all" ? 1 : 0)
    + (filters.anomaliesMode !== "all" ? 1 : 0)
    + (filters.nameHistoryMode !== "all" ? 1 : 0)
    + (filters.ipHistoryMode !== "all" ? 1 : 0)
    + (filters.activityMode !== "all" ? 1 : 0)
    + (sort.key !== defaultSort.key || sort.direction !== defaultSort.direction ? 1 : 0)
}

export function getDiscordFilterLabel(filter: DiscordLinkFilter): string {
  if (filter === "linked") return "С Discord"
  if (filter === "unlinked") return "Без Discord"
  return "Любые"
}

export function getSortDirectionLabel(key: PlayerSortKey, direction: SortDirection): string {
  if (key === "server") return direction === "asc" ? "А — Я" : "Я — А"
  if (key === "play_time") return direction === "asc" ? "Меньше сначала" : "Больше сначала"
  if (key === "active_sanctions" || key === "open_anomalies" || key === "label_count") {
    return direction === "asc" ? "Сначала без" : "Сначала с"
  }
  if (key === "discord_link") return direction === "asc" ? "Сначала без Discord" : "Сначала с Discord"
  return direction === "asc" ? "Сначала старые" : "Сначала новые"
}

function valueForSort(player: FilterablePlayer, key: PlayerSortKey): string | number | null {
  switch (key) {
    case "server":
      return player.server_name ?? null
    case "join_time":
      return player.join_time ?? null
    case "first_login":
      return player.first_login ?? null
    case "last_login":
      return player.last_login_time ?? null
    case "last_activity":
      return player.last_activity ?? player.last_login_time ?? player.join_time ?? null
    case "play_time":
      return player.total_play_time ?? player.play_time ?? (player.play_time_hours != null ? player.play_time_hours * 3600 : null)
    case "active_sanctions":
      return player.has_active_punishments ? 1 : 0
    case "open_anomalies":
      return player.has_open_anomalies ? 1 : 0
    case "discord_link":
      return player.has_discord_link ? 1 : 0
    case "label_count":
      return player.label_ids?.length ?? (player.has_labels ? 1 : 0)
    case "none":
      return null
  }
}

function matchesMode(value: boolean | undefined, mode: FilterMode): boolean {
  if (mode === "all") return true
  return mode === "with" ? value === true : value !== true
}

function matchesActivity(player: FilterablePlayer, mode: ActivityFilter): boolean {
  if (mode === "all") return true
  if (mode === "new") {
    const playTime = valueForSort(player, "play_time")
    return player.is_new_player === true || (typeof playTime === "number" && playTime < 7200)
  }
  const activity = valueForSort(player, "last_activity")
  if (!activity || typeof activity !== "string") return false
  const timestamp = Date.parse(activity)
  if (Number.isNaN(timestamp)) return false
  const days = Number(mode.slice("inactive_".length))
  return Date.now() - timestamp >= days * 24 * 60 * 60 * 1000
}

function matchesLabels(player: FilterablePlayer, filters: PlayerFilterState): boolean {
  const labelIds = player.label_ids ?? []
  if (filters.labelMode === "all") return true
  if (filters.labelMode === "with") return labelIds.length > 0 || player.has_labels === true
  if (filters.labelMode === "without") return labelIds.length === 0 && player.has_labels !== true
  return filters.labelIds.length === 0 || filters.labelIds.some((id) => labelIds.includes(Number(id)))
}

function matchesFilters(player: FilterablePlayer, filters: PlayerFilterState): boolean {
  return (
    (filters.discordFilter === "all"
      || (filters.discordFilter === "linked" && player.has_discord_link)
      || (filters.discordFilter === "unlinked" && !player.has_discord_link))
    && matchesLabels(player, filters)
    && (filters.rankId === "all" || String(player.rank_id ?? "") === filters.rankId)
    && matchesMode(player.has_notes, filters.notesMode)
    && matchesMode(player.has_active_punishments, filters.sanctionsMode)
    && matchesMode(player.has_open_anomalies, filters.anomaliesMode)
    && matchesMode(player.has_previous_names, filters.nameHistoryMode)
    && matchesMode(player.has_ip_history, filters.ipHistoryMode)
    && matchesActivity(player, filters.activityMode)
  )
}

function compareValues(left: string | number | null, right: string | number | null): number {
  if (left == null && right == null) return 0
  if (left == null) return 1
  if (right == null) return -1
  if (typeof left === "number" && typeof right === "number") return left - right
  return String(left).localeCompare(String(right), "ru", { numeric: true, sensitivity: "base" })
}

export function filterAndSortPlayers<T extends FilterablePlayer>(
  players: readonly T[],
  filters: PlayerFilterState,
  sort: PlayerSort,
): T[] {
  return players
    .filter((player) => matchesFilters(player, filters))
    .map((player, index) => ({ player, index }))
    .sort((left, right) => {
      const leftValue = valueForSort(left.player, sort.key)
      const rightValue = valueForSort(right.player, sort.key)
      if (leftValue == null || rightValue == null) {
        return compareValues(leftValue, rightValue)
      }
      const compared = compareValues(leftValue, rightValue)
      return (sort.direction === "asc" ? compared : -compared) || left.index - right.index
    })
    .map(({ player }) => player)
}
