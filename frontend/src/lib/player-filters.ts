export type DiscordLinkFilter = "all" | "linked" | "unlinked"

export type PlayerSortKey =
  | "name"
  | "server"
  | "join_time"
  | "first_login"
  | "last_login"
  | "play_time"

export type SortDirection = "asc" | "desc"

export interface PlayerSort {
  key: PlayerSortKey
  direction: SortDirection
}

export interface FilterablePlayer {
  has_discord_link: boolean
  player_steamid?: string
  player_name?: string | null
  player_last_time_name?: string | null
  server_name?: string | null
  join_time?: string | null
  first_login?: string | null
  last_login_time?: string | null
  play_time?: number | null
  total_play_time?: number | null
  play_time_hours?: number | null
}

function valueForSort(player: FilterablePlayer, key: PlayerSortKey): string | number | null {
  switch (key) {
    case "name":
      return player.player_name || player.player_last_time_name || player.player_steamid || null
    case "server":
      return player.server_name ?? null
    case "join_time":
      return player.join_time ?? null
    case "first_login":
      return player.first_login ?? null
    case "last_login":
      return player.last_login_time ?? null
    case "play_time":
      return player.total_play_time ?? player.play_time ?? (player.play_time_hours != null ? player.play_time_hours * 3600 : null)
  }
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
  discordFilter: DiscordLinkFilter,
  sort: PlayerSort,
): T[] {
  return players
    .filter((player) => (
      discordFilter === "all"
      || (discordFilter === "linked" && player.has_discord_link)
      || (discordFilter === "unlinked" && !player.has_discord_link)
    ))
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
