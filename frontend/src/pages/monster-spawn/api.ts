import { api } from "@/lib/api"

export interface MonsterEntry {
  species: string
  count: number
  scale_with_players: number
  hp_multiplier: number
  hp_scale_with_progress: number
  mobility: number
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
  spawn_type: string
  difficulty: number
  monsters: MonsterEntry[]
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
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const normalizeWave = (value: unknown): SpawnWave | null => {
  if (!isRecord(value)) return null
  return {
    ...value,
    monsters: Array.isArray(value.monsters) ? value.monsters.filter(isRecord) : [],
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

  return {
    ...value,
    config: {
      ...config,
      global: isRecord(config.global) ? config.global : {},
      waves,
    },
    memory: isRecord(value.memory) ? value.memory : {},
    plans,
    telemetry: isRecord(value.telemetry) ? value.telemetry : {},
  }
}

export const monsterSpawnApi = {
  get: (server: string) => api.get<MonsterSpawnState>(`/api/v1/admin/monster-spawn?server=${encodeURIComponent(server)}`).then(normalizeMonsterSpawnState),
  update: (server: string, body: Partial<Pick<MonsterSpawnState, "config" | "memory" | "plans">>) =>
    api.put<MonsterSpawnState>(`/api/v1/admin/monster-spawn?server=${encodeURIComponent(server)}`, body).then(normalizeMonsterSpawnState),
}
