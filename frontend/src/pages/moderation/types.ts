export interface DossierTriageMarker {
  level: "review" | "urgent"
  title: string
  summary: string
  generated_at: string | null
  notice: string
}

export interface OnlinePlayer {
  player_steamid: string
  player_name: string | null
  forced_display_name: string | null
  character_name: string | null
  job_id: string | null
  ping: number | null
  player_id: number | null
  rank_id: number | null
  server_name: string | null
  join_time: string | null
  last_seen: string | null
  total_play_time: number | null
  is_new_player: boolean | null
  is_superuser: boolean
  is_disguised: boolean
  disguise_alias: string | null
  public_display_name: string | null
  disguise_mode: "random" | "fixed" | null
  disguise_owner_name: string | null
  disguise_real_name: string | null
  has_discord_link: boolean
  label_ids: number[]
  has_labels: boolean
  has_notes: boolean
  has_active_punishments: boolean
  has_open_anomalies: boolean
  has_previous_names: boolean
  has_ip_history: boolean
  last_activity: string | null
  dossier_triage?: DossierTriageMarker | null
}

export interface OfflinePlayer {
  player_id: number
  player_steamid: string
  player_last_time_name: string
  forced_display_name: string | null
  rank_id: number | null
  first_login: string | null
  last_login_time: string | null
  last_session_logout: string | null
  play_time_hours: number
  is_new_player: boolean
  is_superuser: boolean
  has_discord_link: boolean
  label_ids: number[]
  has_labels: boolean
  has_notes: boolean
  has_active_punishments: boolean
  has_open_anomalies: boolean
  has_previous_names: boolean
  has_ip_history: boolean
  last_activity: string | null
  dossier_triage?: DossierTriageMarker | null
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
  ranks: Record<string, string>
  label_categories: { id: number; name: string; color: string; icon: string; is_secret: boolean; folder_id: number | null; sort_order: number }[]
  label_folders: { id: number; name: string; sort_order: number }[]
  can_edit: boolean
  can_delete: boolean
  can_issue_punishment: boolean
  can_message: boolean
  can_kick: boolean
  can_ban: boolean
  can_view_disguise_details: boolean
}
