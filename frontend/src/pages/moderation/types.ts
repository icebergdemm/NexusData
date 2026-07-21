export interface OnlinePlayer {
  player_steamid: string
  player_name: string | null
  forced_display_name: string | null
  character_name: string | null
  job_id: string | null
  ping: number | null
  player_id: number | null
  server_name: string | null
  join_time: string | null
  last_seen: string | null
  total_play_time: number | null
  is_new_player: boolean | null
}

export interface OfflinePlayer {
  player_id: number
  player_steamid: string
  player_last_time_name: string
  forced_display_name: string | null
  first_login: string | null
  last_login_time: string | null
  last_session_logout: string | null
  play_time_hours: number
  is_new_player: boolean
}

export interface RoundState {
  active: boolean
  location: string | null
  submarine_name: string | null
  mission: string | null
  started_at: string | null
  players: Record<string, string>[]
  last_end_at: string | null
  last_end_mission_completed: boolean | null
}

export interface AnticheatEvent {
  id: number
  player_steamid: string
  player_name: string | null
  player_id: number | null
  event_type: string
  details: Record<string, unknown> | null
  occurred_at: string | null
  reviewed: boolean
}

export interface ModerationOverview {
  online_players: OnlinePlayer[]
  online_count: number
  online_servers: string[]
  selected_server: string | null
  round_states: Record<string, RoundState>
  recent_offline: OfflinePlayer[]
  search_results: OfflinePlayer[]
  search_query: string | null
  ac_events: AnticheatEvent[]
  can_edit: boolean
  can_delete: boolean
}
