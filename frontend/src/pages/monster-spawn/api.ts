import { api } from "@/lib/api"

export interface MonsterEntry {
  species: string
  count: number
  amount_min: number
  amount_max: number
  max_amount_per_level: number
  scale_with_players: number
  hp_multiplier: number
  hp_scale_with_progress: number
  mobility: number
  /** null = авто-оценка угрозы вида; число 0.3..2.5 = ручная угроза */
  threat_override?: number | null
}

export interface SpawnWave {
  name: string
  min_progress: number
  max_progress: number
  min_round_time: number
  min_players: number
  max_sub_health: number
  max_crew_health: number
  chance: number
  commonness: number
  spawn_type: string
  delay_between_spawns: number
  scatter: number
  offset: number
  spawn_distance: number
  spawn_point_tag?: string
  reset_time: number
  play_dead_probability: number
  difficulty: number
  /** null = авто (abyss/moloch/cave в названии), true/false — явная пометка */
  non_blocking?: boolean | null
  learned?: boolean
  /** null = авто-оценка опасности; число 0.1..5 = ручной множитель веса выбора */
  danger_override?: number | null
  /** Связка волн: имя следующей волны (null/"" = без связки) */
  chain_wave?: string | null
  chain_delay_seconds?: number
  chain_same_position?: boolean
  chain_chance?: number
  monsters: MonsterEntry[]
}

export interface WaveOverride {
  difficulty_mode: "absolute" | "adjust" | string
  difficulty_value: number
  non_blocking?: boolean | null
  enabled?: boolean
  updated_utc?: string
  /** Ручная коррекция опасности волны (множитель веса выбора) */
  danger_mode?: "absolute" | "adjust" | string
  danger_value?: number | null
}

export interface SpeciesDangerOverride {
  threat_value: number
  enabled?: boolean
  updated_utc?: string
}

export interface SpawnScheme {
  name: string
  weight: number
  distance_min_meters: number
  distance_max_meters: number
  zones?: Record<string, number>
}

export interface SpawnPlan extends SpawnWave {
  id: string
  wave_name?: string
  source: "automatic" | "manual"
  status: "planned" | "cancelled" | "spawned" | "expired"
  x: number
  y: number
  note?: string
}

export interface SpawnConfig { global: Record<string, unknown>; waves: SpawnWave[] }
export interface MonsterSpawnState {
  server: string
  config: SpawnConfig
  memory: Record<string, unknown>
  plans: SpawnPlan[]
  telemetry: Record<string, unknown>
  desired_revision: number
  applied_revision: number
  pending: boolean
  updated_at: string | null
  last_seen_at: string | null
  round_reports?: RoundReport[]
  dashboard?: MonsterSpawnDashboard
}

export interface RoundReport {
  id?: number
  analysis_status?: "pending" | "running" | "succeeded" | "failed"
  analysis_text?: string | null
  analysis_error?: string | null
  round_start_utc?: string
  round_end_utc?: string
  round_duration_seconds?: number
  submarine_name?: string
  controller_mode?: string
  summary?: Record<string, unknown>
  waves?: Array<Record<string, unknown>>
  events?: Array<{ time_seconds?: number; direction?: string; kind?: string; payload?: string }>
  [key: string]: unknown
}

export interface MonsterSpawnDashboard {
  round_active: boolean
  round_seconds: number
  submarine_key: string | null
  submarine_name: string | null
  submarine: SubmarineMemory | null
  intensity: number
  event_threshold: number
  pending_waves: number
  known_submarines: number
  blocking_wave_count?: number
  tracked_wave_count?: number
  max_simultaneous_waves?: number
  last_spawn_scheme?: string | null
  last_spawn_zone?: string | null
}

export interface MonsterSpawnWaveLog {
  name: string
  times_spawned: number
  times_unresolved: number
  avg_repel_seconds: number
  avg_performance: number
  avg_crew_deaths: number
  avg_sub_health_loss: number
  last_played_utc?: string | null
}

export interface MonsterSpawnShipLog {
  key: string
  name: string
  type: string
  rounds_played: number
  times_spawned: number
  avg_repel_seconds: number
  performance_ema: number
  avg_crew_deaths: number
  avg_sub_health_loss: number
  recent_performance: number[]
  last_played_utc?: string | null
}

export interface MonsterSpawnRecommendation {
  id: string
  severity: "info" | "warning" | "success"
  title: string
  reason: string
  evidence: Record<string, unknown>
  suggested_adjustment: string
  confidence: number
  read_only: true
}

export interface MonsterSpawnAnalysis {
  generated_at: string
  read_only: true
  global_performance_ema: number
  wave_logs: MonsterSpawnWaveLog[]
  ship_logs: MonsterSpawnShipLog[]
  recommendations: MonsterSpawnRecommendation[]
}

export interface SubmarineMemory {
  name?: string
  type?: string
  rounds_played?: number
  times_spawned?: number
  total_repel_seconds?: number
  total_crew_deaths?: number
  total_sub_health_loss?: number
  performance_ema?: number
  recent_performance?: number[]
  last_played_utc?: string
}

export interface MonsterSpawnTelemetry {
  round_active?: boolean
  round_seconds?: number
  submarine?: string
  submarine_name?: string
  intensity?: number
  event_threshold?: number
  pending_waves?: number
  [key: string]: unknown
}

export interface ActiveWaveTelemetry {
  wave_id: string
  wave_name: string
  spawn_reason: string
  elapsed_seconds: number
  expected_repel_seconds: number
  tracking_timeout_seconds?: number
  monsters_alive: number
  monsters_total: number
  monster_counts: Record<string, number>
  effective_difficulty: number
  base_difficulty?: number
  crew_power_at_start?: number
  chain_depth?: number
  modifications: string[]
  blocking?: boolean
  engaged?: boolean
  /** Корабль ушёл дальше bypass_distance — волна обойдена, бой окончен по факту */
  bypassed?: boolean
  /** Бой идёт прямо сейчас (недавний урон или монстры рядом); engaged залипает */
  in_combat_now?: boolean
  damage_taken?: number
  last_damage_seconds_ago?: number | null
  non_blocking?: boolean
  distance_to_sub_meters?: number | null
  min_distance_meters?: number | null
  max_distance_meters?: number
  unengaged_seconds?: number
  spawn_scheme?: string | null
  spawn_zone?: string | null
}

export interface RecentWaveTelemetry {
  wave_id: string
  wave_name: string
  spawn_reason: string
  outcome: "repelled" | "bypassed" | "afk" | "timeout_engaged" | "unresolved" | string
  /** 7: destroyed — уничтожена; tracking_stopped — отслеживание прекращено (монстры живы) */
  fate?: "destroyed" | "tracking_stopped" | string | null
  repel_seconds: number
  expected_repel_seconds: number
  performance: number
  crew_deaths: number
  sub_health_loss: number
  effective_difficulty: number
  progress_at_spawn: number
  modifications: string
  monster_counts: Record<string, number>
  resolved_at_seconds: number
}

export interface LearnedWaveTelemetry {
  wave_name: string
  event_identifier?: string
  species?: string
  times_seen: number
  first_seen_utc?: string
  last_seen_utc?: string
  registered_in_pool: boolean
  spawn_type?: string
  non_blocking?: boolean
}

export interface SpeciesThreatTelemetry {
  species: string
  threat_ema: number
  effective_threat?: number
  manual_override?: boolean
  samples: number
}

/** Ванильный вид из creature_catalog.json (Content/Characters) */
export interface CreatureInfo {
  id: string
  name_ru?: string
  group?: string
  base_hp?: number
  needs_water?: boolean
  needs_air?: boolean
  combat_strength?: number
  sight?: number
  hearing?: number
  aggression_hurt?: number
  aggression_greed?: number
  flee_health_threshold?: number
  aggressive_boarding?: boolean
}

/** Базовая строка видов из телеметрии плагина (species_baseline) */
export interface SpeciesBaselineTelemetry {
  species: string
  base_hp: number
  base_threat: number
  hp_override?: number
  effective_threat?: number
}

export interface PendingWaveTelemetry {
  wave_name?: string
  spawn_reason?: string
  pos_type?: string
  waiting_seconds?: number
  x?: number
  y?: number
}

export interface HeldVanillaTelemetry {
  wave_name?: string
  species?: string
  hold_remaining_seconds?: number
  original_amount?: number
}

export interface PlannedActionTelemetry {
  action: string
  probability: number
  detail: string
  next_spawn_possible_at?: number
  checks?: Record<string, string | number | boolean>
}

export interface LLMRun {
  id: string
  mode: string
  instruction: string
  requested_at: string
  requested_by: number | null
  attempts: number
}

export interface LLMRuntime {
  enabled: boolean
  auto_apply: boolean
  auto_interval_minutes: number
  live_analysis_enabled: boolean
  live_control_enabled: boolean
  live_analysis_interval_seconds: number
  last_run_at: string | null
  history: { at: string; mode: string; summary: string; applied: number; rejected: number; confidence: number }[]
  pending_run: LLMRun | null
  active_run: (LLMRun & { started_at: string; lease_expires_at: string }) | null
}

export interface LLMRunRecord {
  id: number
  run_id: string
  server: string
  mode: "analyze" | "apply" | "spawn" | string
  source: "manual" | "auto" | "live" | string
  status: "queued" | "running" | "succeeded" | "failed" | string
  instruction: string
  requested_by_admin_id: number | null
  requested_at: string | null
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  confidence: number | null
  applied_count: number
  rejected_count: number
  spawned_count: number
  error_message: string
  result: {
    situation?: string
    crew_summary?: string
    scores?: {
      crew_effectiveness: number | null
      monster_strength: number | null
      wave_pressure: number | null
      ship_efficiency: number | null
      basis?: string
    }
    live_metrics?: Record<string, unknown>
    live_proposal?: { action: string; reason_code: string; evidence: string[]; hold_seconds: number }
    live_execution?: { status: string; action?: string; reason?: string }
    findings?: { kind: string; detail: string; evidence: string }[]
    recommendations?: { title: string; detail: string }[]
    applied_changes?: { key: string; new_value: unknown; old_value?: unknown; reason?: string }[]
    proposed_changes?: { key: string; new_value: unknown; old_value?: unknown; reason?: string }[]
    change_decisions?: Record<string, "accepted" | "rejected">
    rejected?: string[]
    spawn_requests?: { species: string; count: number; hp_multiplier: number; position_hint: string; reason?: string }[]
    plan_ids?: string[]
  } | null
}

export interface LLMDeployment {
  base_url: string
  model: string
  temperature: number
  top_p: number
  timeout_seconds: number
  api_key_set: boolean
  has_base_url: boolean
}

export const telemetryList = <T,>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : [])

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const normalizeWave = (value: unknown): SpawnWave | null => {
  if (!isRecord(value)) return null
  const monsters = Array.isArray(value.monsters) ? value.monsters : []
  return {
    ...value,
    // Регистронезависимость особых волн: spawn_type приводим к каноническим
    // алиасам в нижнем регистре ("Cave"/"AbyssCave" из PosType.ToString() и
    // т.п.), чтобы селекты и фильтры работали одинаково.
    spawn_type: typeof value.spawn_type === "string" ? value.spawn_type.trim().toLowerCase() : value.spawn_type,
    commonness: typeof value.commonness === "number" ? value.commonness : 1,
    delay_between_spawns: typeof value.delay_between_spawns === "number" ? value.delay_between_spawns : 0.1,
    scatter: typeof value.scatter === "number" ? value.scatter : 500,
    offset: typeof value.offset === "number" ? value.offset : 0,
    spawn_distance: typeof value.spawn_distance === "number" ? value.spawn_distance : 0,
    reset_time: typeof value.reset_time === "number" ? value.reset_time : 0,
    play_dead_probability: typeof value.play_dead_probability === "number" ? value.play_dead_probability : -1,
    monsters: monsters.filter(isRecord).map((monster) => ({
      ...monster,
      amount_min: typeof monster.amount_min === "number" ? monster.amount_min : 0,
      amount_max: typeof monster.amount_max === "number" ? monster.amount_max : 0,
      max_amount_per_level: typeof monster.max_amount_per_level === "number" ? monster.max_amount_per_level : 0,
      threat_override: typeof monster.threat_override === "number" ? monster.threat_override : null,
    })),
    danger_override: typeof value.danger_override === "number" ? value.danger_override : null,
    chain_wave: typeof value.chain_wave === "string" ? value.chain_wave : null,
    chain_delay_seconds: typeof value.chain_delay_seconds === "number" ? value.chain_delay_seconds : 0,
    chain_same_position: typeof value.chain_same_position === "boolean" ? value.chain_same_position : true,
    chain_chance: typeof value.chain_chance === "number" ? value.chain_chance : 1,
  } as unknown as SpawnWave
}

/**
 * Runtime API data can come from an older plugin snapshot. Keep collection
 * fields safe before React renders or edits them instead of trusting the TS
 * interface at the network boundary.
 */
export const normalizeMonsterSpawnState = (value: MonsterSpawnState): MonsterSpawnState => {
  const config: Record<string, unknown> = isRecord(value.config) ? value.config : {}
  const waves = Array.isArray(config.waves)
    ? config.waves.map(normalizeWave).filter((wave): wave is SpawnWave => wave !== null)
    : []
  const plans = Array.isArray(value.plans)
    ? value.plans.map(normalizeWave).filter((plan): plan is SpawnWave => plan !== null) as SpawnPlan[]
    : []
  const telemetry = isRecord(value.telemetry) ? value.telemetry as MonsterSpawnTelemetry : {}
  const memory = isRecord(value.memory) ? value.memory : {}
  const submarines = isRecord(memory.submarines) ? memory.submarines : {}
  const currentKey = typeof telemetry.submarine === "string" ? telemetry.submarine : null
  const currentSubmarine = currentKey && isRecord(submarines[currentKey]) ? submarines[currentKey] as SubmarineMemory : null

  return {
    ...value,
    config: {
      ...config,
      global: isRecord(config.global) ? config.global : {},
      waves,
    },
    memory,
    plans,
    telemetry,
    dashboard: value.dashboard ?? {
      round_active: telemetry.round_active === true,
      round_seconds: typeof telemetry.round_seconds === "number" ? telemetry.round_seconds : 0,
      submarine_key: currentKey,
      submarine_name: typeof telemetry.submarine_name === "string" ? telemetry.submarine_name : currentSubmarine?.name ?? null,
      submarine: currentSubmarine,
      intensity: typeof telemetry.intensity === "number" ? telemetry.intensity : 0,
      event_threshold: typeof telemetry.event_threshold === "number" ? telemetry.event_threshold : 0.2,
      pending_waves: typeof telemetry.pending_waves === "number" ? telemetry.pending_waves : 0,
      known_submarines: Object.keys(submarines).length,
       blocking_wave_count: typeof telemetry.blocking_wave_count === "number" ? telemetry.blocking_wave_count : 0,
       tracked_wave_count: typeof telemetry.tracked_wave_count === "number" ? telemetry.tracked_wave_count : 0,
      max_simultaneous_waves: typeof telemetry.max_simultaneous_waves === "number" ? telemetry.max_simultaneous_waves : 1,
      last_spawn_scheme: typeof telemetry.last_spawn_scheme === "string" ? telemetry.last_spawn_scheme : null,
      last_spawn_zone: typeof telemetry.last_spawn_zone === "string" ? telemetry.last_spawn_zone : null,
    },
  }
}

export const monsterSpawnApi = {
  get: (server: string) => api.get<MonsterSpawnState>(`/api/v1/admin/monster-spawn?server=${encodeURIComponent(server)}`).then(normalizeMonsterSpawnState),
  update: (server: string, body: Partial<Pick<MonsterSpawnState, "config" | "memory" | "plans">>) =>
    api.put<MonsterSpawnState>(`/api/v1/admin/monster-spawn?server=${encodeURIComponent(server)}`, body).then(normalizeMonsterSpawnState),
  resetAdaptations: (server: string) =>
    api.post<MonsterSpawnState>(`/api/v1/admin/monster-spawn/reset-adaptations?server=${encodeURIComponent(server)}`, {}).then(normalizeMonsterSpawnState),
  analysis: (server: string) => api.get<MonsterSpawnAnalysis>(`/api/v1/admin/monster-spawn/analysis?server=${encodeURIComponent(server)}`),
  reports: (server: string) => api.get<{ server: string; reports: RoundReport[] }>(`/api/v1/admin/monster-spawn/reports?server=${encodeURIComponent(server)}`),
  retryRoundAnalysis: (server: string, reportId: number) =>
    api.post<{ status: string }>(`/api/v1/admin/monster-spawn/reports/${reportId}/retry?server=${encodeURIComponent(server)}`, {}),
  creatures: () => api.get<{ creatures: CreatureInfo[] }>(`/api/v1/admin/monster-spawn/creatures`),
  llmRuntime: (server: string) =>
    api.get<{ runtime: LLMRuntime; deployment: LLMDeployment; endpoint_configured: boolean }>(`/api/v1/admin/monster-spawn/llm/runtime?server=${encodeURIComponent(server)}`),
  llmRuns: (server: string, limit = 30) =>
    api.get<{ runs: LLMRunRecord[] }>(`/api/v1/admin/monster-spawn/llm/runs?server=${encodeURIComponent(server)}&limit=${limit}`),
  updateLLMRuntime: (server: string, body: Partial<Pick<LLMRuntime, "enabled" | "auto_apply" | "auto_interval_minutes" | "live_analysis_enabled" | "live_control_enabled" | "live_analysis_interval_seconds">>) =>
    api.put<{ status: string; runtime: LLMRuntime }>(`/api/v1/admin/monster-spawn/llm/runtime?server=${encodeURIComponent(server)}`, body),
  runLLM: (server: string, body: { mode: "analyze" | "apply" | "spawn"; instruction?: string }) =>
    api.post<{ status: string; run: LLMRun }>(`/api/v1/admin/monster-spawn/llm/run?server=${encodeURIComponent(server)}`, body),
  cancelLLMRun: (server: string, runId: string) =>
    api.post<{ status: string }>(`/api/v1/admin/monster-spawn/llm/runs/${encodeURIComponent(runId)}/cancel?server=${encodeURIComponent(server)}`, {}),
  applyProposal: (server: string, runId: string, changeIndices?: number[], decision: "accepted" | "rejected" = "accepted") =>
    api.post<{ status: string; revision?: number }>(`/api/v1/admin/monster-spawn/llm/apply-proposal?server=${encodeURIComponent(server)}`, { run_id: runId, change_indices: changeIndices, decision }),
}
