export interface PlayerDetailPlayer {
  player_id: number
  player_last_time_name: string
  player_steamid: string
  last_login_time: string | null
  play_time: number
  play_time_hours: number
  rank_id: number
  kit_level: number
  last_ip: string | null
  discord_id: string | null
  discord_id_confirmed: boolean
  is_secret: boolean
}

export interface PlayerStats {
  successful_missions_captain: number
  total_missions_captain: number
  saved_players: number
  killed_monsters: number
  killed_players: number
  killed_npc: number
}

export interface Note {
  id: number
  admin_id: number | null
  content: string
  created_at: string | null
  updated_at: string | null
}

export interface LabelCategory {
  id: number
  name: string
  color: string
  icon: string
}

export interface LabelPair {
  id: number
  note: string | null
  assigned_at: string | null
  category: LabelCategory
}

export interface Trap {
  id: number
  target_steamid: string
  reason: string | null
  set_at: string | null
  active: boolean
  triggered_at: string | null
}

export interface PunishmentRow {
  id: number
  type: string
  reason: string | null
  issued_by_name: string | null
  issued_at: string | null
  expires_at: string | null
  is_active: boolean
  notes: string | null
  status: "active" | "expired" | "void"
}

export interface SessionRow {
  id: number
  session_number: number
  login_time: string | null
  logout_time: string | null
  session_duration: number | null
  avg_ping: number | null
}

export interface IpRow { id: number; ip: string; subnet: string; first_seen: string | null; last_seen: string | null }
export interface NameRow { id: number; nickname: string; first_seen: string | null; last_seen: string | null }
export interface KillRow { id: number; monster_type: string; kills_count: number }
export interface RoleStatRow { id: number; role: string; rounds_played: number; mvp_score: number }
export interface RoleActionRow { id: number; role: string; action_type: string; value: number }

export interface AvailableKvalKit { job: string; level: number; label: string }

export interface PlayerDetailResponse {
  classified?: boolean
  player: PlayerDetailPlayer
  stats: PlayerStats | null
  kills: KillRow[]
  role_actions: RoleActionRow[]
  ips: IpRow[]
  names: NameRow[]
  role_stats: RoleStatRow[]
  sessions: SessionRow[]
  notes: Note[]
  label_pairs: LabelPair[]
  traps: Trap[]
  label_categories: LabelCategory[]
  punishments: PunishmentRow[]
  ranks: Record<string, string>
  kits: Record<string, string>
  kval_kits: Record<string, number>
  custom_items: Record<string, number>
  custom_stats: Record<string, number | Record<string, number>>
  custom_talents: Record<string, string[]>
  available_kval_kits: AvailableKvalKit[]
  is_online: boolean
  has_view: boolean
  has_edit: boolean
  has_delete: boolean
  has_special: boolean
}
