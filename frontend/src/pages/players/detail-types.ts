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
  /** Direct Discord ID or the Discord ID of the linked NexusData account. */
  discord_profile_id: string | null
  discord_id_confirmed: boolean
  is_secret: boolean
  linked_admin_id: number | null
  linked_admin_name: string | null
  forced_display_name: string | null
  steam_avatar_url: string | null
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
  admin_name: string | null
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
  issued_by_was_superuser: boolean
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

export interface RewardJob { identifier: string; name: string }
export interface RewardTalent { identifier: string; name: string }

/** Справочник для форм выдачи наград (routes/admin.py: _rewards_reference) —
 * профессии, навыки и таланты с русскими названиями, чтобы идентификаторы не
 * приходилось вписывать руками. */
export interface RewardReference {
  jobs: RewardJob[]
  skill_labels: Record<string, string>
  talents: Record<string, RewardTalent[]>
}

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
  /** Предметы, привязанные к профессии: {профессия: {item_id: количество}}.
   * Отдельно от custom_items, потому что `items` в контракте с игровым
   * сервером — плоский {item_id: количество}, и его форму менять нельзя. */
  custom_job_items: Record<string, Record<string, number>>
  custom_stats: Record<string, number | Record<string, number>>
  custom_talents: Record<string, string[]>
  available_kval_kits: AvailableKvalKit[]
  reward_reference: RewardReference
  is_online: boolean
  // A player can hold several grants at once — one manual per server_scope
  // (global/ASPER1/ASPER2) plus one department-derived (is_auto) grant,
  // summed together rather than only one ever existing.
  server_privileges: PlayerServerPrivilege[]
  has_view: boolean
  has_edit: boolean
  /** Гранулярное право на выдачу наград (players:manage_rewards). Отдельно
   * от has_edit: ручки наград проверяют именно его, и без этого флага
   * интерфейс показывал бы кнопки, которые возвращают 403. */
  can_manage_rewards: boolean
  /** players:set_forced_name — установка принудительного имени без привязки
   * к аккаунту ND. Отдельно от has_edit/is_superuser: ручка привязки
   * аккаунта (/link-admin) остаётся только для суперов, эта — нет. */
  can_set_forced_name: boolean
  /** players:manage_notes — отдельно от has_edit по той же причине, что и
   * выше: у заметок личного дела своё собственное право, не общий "edit". */
  can_manage_notes: boolean
  /** players:manage_labels — назначение и снятие меток в профиле игрока. */
  can_manage_labels: boolean
  has_delete: boolean
  has_special: boolean
  can_issue_punishment: boolean
  /** Право «Наказания → отзыв»: отмена наказания и разбан прямо здесь, в
   *  профиле, а не только на общей странице наказаний. */
  can_revoke_punishment: boolean
  punishment_types: string[]
  punishment_type_labels: Record<string, string>
}

export interface AIDossierEvidence {
  kind: string
  id: string | number
  href?: string
}

export type AIDossierTriage = "none" | "review" | "urgent"
export type AIDossierSeverity = "low" | "medium" | "high"

export interface AIDossierFinding {
  category: string
  severity: AIDossierSeverity
  title: string
  details: string
  reason: string
  next_step: string
  evidence: AIDossierEvidence[]
}

export interface AIDossierContext {
  kind: string
  text: string
  source_id?: string | number
  href?: string
}

export interface AIDossierResult {
  summary: string
  schema_version: 2
  triage: { level: AIDossierTriage; title: string; reason: string }
  summary_source?: "rules" | "qwen"
  findings: AIDossierFinding[]
  context: AIDossierContext[]
  caveats: string[]
  confidence: "low" | "medium" | "high"
}

export interface AIDossierResponse {
  status: "none" | "queued" | "running" | "succeeded" | "failed"
  result: AIDossierResult | null
  stale: boolean
  summary: string | null
  model: string | null
  requested_at: string | null
  started_at: string | null
  generated_at: string | null
  finished_at: string | null
  error: string | null
  requested_by_admin_id: number | null
  run?: {
    source: string
    status: string
    latency_ms: number | null
    prompt_tokens: number | null
    completion_tokens: number | null
    requested_by_admin_id: number | null
    raw_response?: string | null
  } | null
}

export interface PlayerServerPrivilege {
  id: number
  server_group_id: number | null
  server_group_name: string | null
  server_scope: string | null
  // True when task_sync_auto_server_privileges assigned this group from a
  // department access level/head role, rather than a human via the Server
  // Privileges page — shown as "От отдела: X" instead of a manual grant.
  is_auto: boolean
  auto_source_department_name: string | null
}
