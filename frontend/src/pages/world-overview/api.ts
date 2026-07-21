import { api } from "@/lib/api"

export interface WorldRoundSummary {
  round_id: string
  server_name: string | null
  level_name: string | null
  sub_name: string | null
  t: number | null
}

export interface WorldSubmarine {
  id: number
  name: string
}

// Sub-local rect: [x, y, w, h] — relative to that submarine's position.
export type Rect = [number, number, number, number]

export interface WorldHullGeometry {
  id: number
  sub: number
  r: Rect
}

export interface WorldWallGeometry {
  sub: number
  r: Rect
  h: boolean
  p: boolean
}

export interface WorldDoorGeometry {
  id: number
  sub: number
  r: Rect
  h: boolean
}

export interface WorldGeometry {
  round_id: string
  server_name: string | null
  level_seed: string | null
  level_name: string | null
  level_type: string | null
  level_w: number | null
  level_h: number | null
  sub_name: string | null
  submarines: WorldSubmarine[]
  hulls: WorldHullGeometry[]
  walls: WorldWallGeometry[]
  doors: WorldDoorGeometry[]
  outline_w: number | null
  outline_h: number | null
  outline: [number, number][][]
}

export interface WorldSubPosition {
  id: number
  x: number
  y: number
}

export interface WorldHullState {
  id: number
  water: number
  oxygen: number
  fire: number
}

export interface WorldDoorState {
  id: number
  open: number
}

export interface WorldReactor {
  id: number
  sub: number
  power_on: boolean
  fission: number
  turbine: number
  load: number
  max_output: number
  temp: number
  fuel: number
  critical: boolean
  melted: boolean
}

export type WorldTeam = "Team1" | "Team2" | "None" | "FriendlyNPC"

export interface WorldCharacter {
  id: number
  name: string
  species: string
  human: boolean
  team: WorldTeam
  job: string | null
  steamid: string | null
  x: number
  y: number
  hp: number
  oxygen: number
  dead: boolean
  water: boolean
  ragdoll: boolean
  dir: number
  hull: number | null
}

export type WorldItemOwnerType = "character" | "item" | "hull" | "world"

export interface WorldItem {
  id: number
  item: string | null
  x: number
  y: number
  cond: number
  owner_t: WorldItemOwnerType
  owner_id: number | null
}

export interface WorldState {
  round_id: string
  seq: number | null
  t: number | null
  subs: WorldSubPosition[]
  hulls_state: WorldHullState[]
  doors_state: WorldDoorState[]
  reactors: WorldReactor[]
  characters: WorldCharacter[]
  items: WorldItem[]
  last_update_at: string | null
  is_active: boolean
}

const base = "/api/v1/admin/world"

export const worldStateApi = {
  listRounds: () => api.get<{ rounds: WorldRoundSummary[] }>(`${base}/rounds`),
  getGeometry: (roundId: string) => api.get<WorldGeometry>(`${base}/rounds/${roundId}/geometry`),
  getState: (roundId: string) => api.get<WorldState>(`${base}/rounds/${roundId}/state`),
}

/** A character's inventory — items[] is a flat list keyed by owner_t/owner_id
 * (per WORLD_STATE_API_GUIDE.md, never duplicated inside characters[]).
 * Resolves direct holdings (owner_t="character") plus one level of nested
 * container contents (owner_t="item" pointing at one of those held items) —
 * enough to show e.g. a backpack's contents without unbounded recursion. */
export function resolveCharacterItems(state: WorldState, characterId: number): WorldItem[] {
  const direct = state.items.filter((it) => it.owner_t === "character" && it.owner_id === characterId)
  const containerIds = new Set(direct.map((it) => it.id))
  const nested = state.items.filter((it) => it.owner_t === "item" && it.owner_id != null && containerIds.has(it.owner_id))
  return [...direct, ...nested]
}
