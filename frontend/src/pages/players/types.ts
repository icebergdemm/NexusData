export interface PlayerRow {
  player_id: number
  player_last_time_name: string
  forced_display_name?: string | null
  player_steamid: string
  rank_id: number
  kit_level: number
  play_time: number
  play_time_hours: number
  last_login_time: string
  rank_name?: string | null
  kit_name?: string | null
}

export interface LabelCategory {
  id: number
  name: string
  color: string
  icon: string
}

export interface PlayersListResponse {
  players: PlayerRow[]
  page: number
  page_size: number
  total: number
  total_pages: number
  label_categories: LabelCategory[]
  selected_label_id: number | null
  selected_rank_id: number | null
  selected_sort: string | null
  ranks: Record<string, string>
  rank_colors: Record<string, string>
}
